"""Bounded background execution for local read-only queries."""

from collections.abc import Callable
from dataclasses import dataclass
from queue import Empty, Full, Queue
from threading import Event, Lock, Thread, current_thread

from llm_agent.application.services.queries import (
    QUERY_INVALID_REQUEST,
    QUERY_IO_FAILED,
    QueryCancellation,
    WorkspaceQueryKind,
    WorkspaceQueryRequest,
    WorkspaceQueryResult,
    WorkspaceQueryStatus,
)

QueryFunction = Callable[[WorkspaceQueryRequest, QueryCancellation], WorkspaceQueryResult]
QUERY_SETTLEMENT_TIMEOUT_SECONDS = 2.0


@dataclass(frozen=True, slots=True)
class CliQuerySubmission:
    query_generation: int
    workspace_id: str
    workspace_generation: int
    task_active: bool
    request: WorkspaceQueryRequest


@dataclass(frozen=True, slots=True)
class CliQueryCompletion:
    query_generation: int
    workspace_id: str
    workspace_generation: int
    task_active: bool
    kind: WorkspaceQueryKind
    result: WorkspaceQueryResult | None
    adapter_reason_code: str | None = None
    live_marker: str | None = None


class _EventCancellation:
    def __init__(self, event: Event) -> None:
        self._event = event

    def is_cancelled(self) -> bool:
        return self._event.is_set()


class BoundedQueryExecutor:
    """CLI-owned single-worker scheduling and generation correlation."""

    def __init__(
        self,
        *,
        workspace_id: str,
        workspace_generation: int = 1,
        live_marker_factory: Callable[[bool], str | None] | None = None,
    ) -> None:
        self._live_marker_factory = live_marker_factory or (lambda _active: None)
        self.workspace_id = workspace_id
        self.workspace_generation = workspace_generation
        self._lock = Lock()
        self._next_generation = 1
        self._worker: Thread | None = None
        self._cancel: Event | None = None
        self._active_submission: CliQuerySubmission | None = None
        self._channel: Queue[CliQueryCompletion] = Queue(maxsize=1)
        self._result_pending = False
        self._dropped_late = 0

    @property
    def dropped_late(self) -> int:
        with self._lock:
            return self._dropped_late

    def is_busy(self) -> bool:
        with self._lock:
            return self._worker is not None or self._result_pending

    def _busy_completion(
        self,
        request: WorkspaceQueryRequest,
        task_active: bool,
    ) -> CliQueryCompletion:
        return CliQueryCompletion(
            query_generation=0,
            workspace_id=self.workspace_id,
            workspace_generation=self.workspace_generation,
            task_active=task_active,
            kind=request.kind,
            result=None,
            adapter_reason_code="QUERY_BUSY",
        )

    def _start_worker(
        self,
        submission: CliQuerySubmission,
        cancellation: _EventCancellation,
        execute: QueryFunction,
    ) -> None:
        worker = Thread(
            target=self._run_query,
            args=(submission, cancellation, execute),
            name=f"query-worker-{submission.query_generation}",
            daemon=False,
        )
        self._worker = worker
        try:
            worker.start()
        except Exception:
            if worker.ident is None:
                self._worker = None
                self._cancel = None
                self._active_submission = None
                raise
            # Thread.start can report an error after the OS thread exists.
            # Keep the accepted work owned and let the normal result path settle it.

    def _execute_query(
        self,
        submission: CliQuerySubmission,
        cancellation: _EventCancellation,
        execute: QueryFunction,
    ) -> WorkspaceQueryResult:
        try:
            result = execute(submission.request, cancellation)
            if not isinstance(result, WorkspaceQueryResult) or result.kind is not submission.request.kind:
                return WorkspaceQueryResult(
                    submission.request.kind,
                    WorkspaceQueryStatus.FAILED,
                    reason_code=QUERY_INVALID_REQUEST,
                    error="query worker returned an invalid canonical result",
                )
            return result
        except BaseException as exc:
            return WorkspaceQueryResult(
                submission.request.kind,
                WorkspaceQueryStatus.FAILED,
                reason_code=QUERY_IO_FAILED,
                error=f"query worker: {type(exc).__name__}: {exc}"[:2_000],
            )

    def _run_query(
        self,
        submission: CliQuerySubmission,
        cancellation: _EventCancellation,
        execute: QueryFunction,
    ) -> None:
        result = self._execute_query(submission, cancellation, execute)
        try:
            live_marker = self._live_marker_factory(submission.task_active)
        except Exception:
            # A UI hint must not strand the canonical query result.
            live_marker = None
        completion = CliQueryCompletion(
            query_generation=submission.query_generation,
            workspace_id=submission.workspace_id,
            workspace_generation=submission.workspace_generation,
            task_active=submission.task_active,
            kind=submission.request.kind,
            result=result,
            live_marker=live_marker,
        )
        self._publish_completion(completion)

    def _publish_completion(self, completion: CliQueryCompletion) -> None:
        with self._lock:
            try:
                try:
                    self._channel.put_nowait(completion)
                except Full:
                    self._dropped_late += 1
                else:
                    self._result_pending = True
            finally:
                if self._worker is current_thread():
                    self._worker = None
                    self._cancel = None
                    self._active_submission = None

    def submit(
        self,
        request: WorkspaceQueryRequest,
        *,
        task_active: bool,
        execute: QueryFunction,
    ) -> CliQuerySubmission | CliQueryCompletion:
        self._validate_submission(request, execute)
        with self._lock:
            if self._worker is not None or self._result_pending:
                return self._busy_completion(request, task_active)
            generation = self._next_generation
            self._next_generation += 1
            submission = CliQuerySubmission(
                generation,
                self.workspace_id,
                self.workspace_generation,
                task_active,
                request,
            )
            cancel_event = Event()
            cancellation = _EventCancellation(cancel_event)
            self._cancel = cancel_event
            self._active_submission = submission
            self._start_worker(submission, cancellation, execute)
            return submission

    @staticmethod
    def _validate_submission(request: WorkspaceQueryRequest, execute: QueryFunction) -> None:
        if not isinstance(request, WorkspaceQueryRequest):
            raise TypeError("request must be WorkspaceQueryRequest")
        if not callable(execute):
            raise TypeError("execute must be callable")

    def poll(self) -> CliQueryCompletion | None:
        with self._lock:
            try:
                completion = self._channel.get_nowait()
            except Empty:
                return None
            self._result_pending = False
            if (
                completion.workspace_id != self.workspace_id
                or completion.workspace_generation != self.workspace_generation
            ):
                self._dropped_late += 1
                return None
        return completion

    def request_cancel(self) -> bool:
        with self._lock:
            cancel = self._cancel
        if cancel is None:
            return False
        cancel.set()
        return True

    def wait_for_result(self, *, timeout_seconds: float | None = None) -> CliQueryCompletion | None:
        """Wait without requesting cancellation; UI adapters may use Futures."""
        with self._lock:
            worker = self._worker
        if worker is not None:
            worker.join(None if timeout_seconds is None else max(0.0, timeout_seconds))
            if worker.is_alive():
                return None
        return self.poll()

    def cancel_and_wait(
        self, *, timeout_seconds: float = QUERY_SETTLEMENT_TIMEOUT_SECONDS
    ) -> CliQueryCompletion | None:
        with self._lock:
            worker = self._worker
            cancel = self._cancel
            submission = self._active_submission
        if cancel is not None:
            cancel.set()
        if worker is not None:
            worker.join(max(0.0, timeout_seconds))
            if worker.is_alive():
                if submission is None:
                    return None
                return CliQueryCompletion(
                    query_generation=submission.query_generation,
                    workspace_id=submission.workspace_id,
                    workspace_generation=submission.workspace_generation,
                    task_active=submission.task_active,
                    kind=submission.request.kind,
                    result=None,
                    adapter_reason_code="QUERY_SHUTDOWN_TIMEOUT",
                )
        return self.poll()


__all__ = [
    "BoundedQueryExecutor",
    "CliQueryCompletion",
    "CliQuerySubmission",
    "QueryFunction",
]

QuerySubmission = CliQuerySubmission
QueryCompletion = CliQueryCompletion

"""Bounded background execution for local read-only queries."""
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from queue import Empty, Full, Queue
from threading import Event, Lock, Thread

from agent.application_services.queries import (
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


def _live_marker(task_active: bool) -> str | None:
    if not task_active:
        return None
    return f"LIVE SNAPSHOT | task active | {datetime.now(timezone.utc).isoformat()}"


class BoundedQueryExecutor:
    """CLI-owned single-worker scheduling and generation correlation."""

    def __init__(self, *, workspace_id: str, workspace_generation: int = 1) -> None:
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

    def submit(
        self,
        request: WorkspaceQueryRequest,
        *,
        task_active: bool,
        execute: QueryFunction,
    ) -> CliQuerySubmission | CliQueryCompletion:
        if not isinstance(request, WorkspaceQueryRequest):
            raise TypeError("request must be WorkspaceQueryRequest")
        if not callable(execute):
            raise TypeError("execute must be callable")
        with self._lock:
            if self._worker is not None or self._result_pending:
                return CliQueryCompletion(
                    query_generation=0,
                    workspace_id=self.workspace_id,
                    workspace_generation=self.workspace_generation,
                    task_active=task_active,
                    kind=request.kind,
                    result=None,
                    adapter_reason_code="QUERY_BUSY",
                )
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

            def run() -> None:
                try:
                    result = execute(request, cancellation)
                    if not isinstance(result, WorkspaceQueryResult) or result.kind is not request.kind:
                        result = WorkspaceQueryResult(
                            request.kind,
                            WorkspaceQueryStatus.FAILED,
                            reason_code=QUERY_INVALID_REQUEST,
                            error="query worker returned an invalid canonical result",
                        )
                except BaseException as exc:
                    result = WorkspaceQueryResult(
                        request.kind,
                        WorkspaceQueryStatus.FAILED,
                        reason_code=QUERY_IO_FAILED,
                        error=f"query worker: {type(exc).__name__}: {exc}"[:2_000],
                    )
                completion = CliQueryCompletion(
                    query_generation=submission.query_generation,
                    workspace_id=submission.workspace_id,
                    workspace_generation=submission.workspace_generation,
                    task_active=submission.task_active,
                    kind=submission.request.kind,
                    result=result,
                    live_marker=_live_marker(submission.task_active),
                )
                with self._lock:
                    try:
                        self._channel.put_nowait(completion)
                    except Full:
                        self._dropped_late += 1
                    else:
                        self._result_pending = True
                    self._worker = None
                    self._cancel = None
                    self._active_submission = None

            worker = Thread(target=run, name=f"query-worker-{generation}", daemon=False)
            self._worker = worker
            worker.start()
            return submission

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

    def cancel_and_wait(self, *, timeout_seconds: float = QUERY_SETTLEMENT_TIMEOUT_SECONDS) -> CliQueryCompletion | None:
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

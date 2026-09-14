"""Bounded background execution for local read-only queries."""

from __future__ import annotations

from queue import Empty, Full, Queue
from threading import Event, Lock, Thread
from typing import Callable

from agent.interfaces.cli.query_plane import QueryRequest, QueryResult

QueryFunction = Callable[[QueryRequest, Event], QueryResult]
QUERY_SETTLEMENT_TIMEOUT_SECONDS = 2.0


class BoundedQueryExecutor:
    """At most one non-agentic query worker; no implicit queue."""

    def __init__(self, *, workspace_id: str, workspace_generation: int = 1) -> None:
        self.workspace_id = workspace_id
        self.workspace_generation = workspace_generation
        self._lock = Lock()
        self._next_generation = 1
        self._worker: Thread | None = None
        self._cancel: Event | None = None
        self._active_request: QueryRequest | None = None
        self._channel: Queue[QueryResult] = Queue(maxsize=1)
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
        command_id: str,
        arguments: dict[str, object],
        *,
        task_active: bool,
        execute: QueryFunction,
    ) -> QueryResult | QueryRequest:
        with self._lock:
            if self._worker is not None or self._result_pending:
                return QueryResult(0, self.workspace_id, self.workspace_generation, command_id, False, error="QUERY_BUSY")
            generation = self._next_generation
            self._next_generation += 1
            request = QueryRequest(
                generation,
                self.workspace_id,
                self.workspace_generation,
                command_id,
                dict(arguments),
                task_active,
            )
            cancel = Event()
            self._cancel = cancel
            self._active_request = request

            def run() -> None:
                try:
                    result = execute(request, cancel)
                except BaseException as exc:
                    result = QueryResult(
                        request.query_generation,
                        request.workspace_id,
                        request.workspace_generation,
                        request.command_id,
                        False,
                        error=f"query worker: {type(exc).__name__}: {exc}",
                    )
                with self._lock:
                    try:
                        self._channel.put_nowait(result)
                    except Full:
                        self._dropped_late += 1
                    else:
                        self._result_pending = True
                    self._worker = None
                    self._cancel = None
                    self._active_request = None

            worker = Thread(target=run, name=f"query-worker-{generation}", daemon=False)
            self._worker = worker
            worker.start()
            return request

    def poll(self) -> QueryResult | None:
        with self._lock:
            try:
                result = self._channel.get_nowait()
            except Empty:
                return None
            self._result_pending = False
            if result.workspace_id != self.workspace_id or result.workspace_generation != self.workspace_generation:
                self._dropped_late += 1
                return None
        return result

    def request_cancel(self) -> bool:
        with self._lock:
            cancel = self._cancel
        if cancel is None:
            return False
        cancel.set()
        return True

    def cancel_and_wait(self, *, timeout_seconds: float = QUERY_SETTLEMENT_TIMEOUT_SECONDS) -> QueryResult | None:
        with self._lock:
            worker = self._worker
            cancel = self._cancel
            request = self._active_request
        if cancel is not None:
            cancel.set()
        if worker is not None:
            worker.join(max(0.0, timeout_seconds))
            if worker.is_alive():
                if request is None:
                    return None
                return QueryResult(
                    request.query_generation,
                    request.workspace_id,
                    request.workspace_generation,
                    request.command_id,
                    False,
                    error="QUERY_SHUTDOWN_TIMEOUT",
                )
        return self.poll()


__all__ = ["BoundedQueryExecutor", "QueryFunction"]

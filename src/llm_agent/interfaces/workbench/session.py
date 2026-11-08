"""One serial background worker owns the runtime; Tk owns all widgets."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from threading import Event, Lock
from typing import Any

from llm_agent.application.approval import ApprovalPrompt, ApprovalResponse
from llm_agent.application.task_execution import (
    TaskExecutionRuntime,
    TaskExecutionStart,
    bind_submission_cancellation,
    close_task_execution,
    execute_submission,
    read_execution_context,
    start_interactive_session,
)
from llm_agent.application.task_results import InteractiveTaskObservation
from llm_agent.application.workbench.artifacts import WorkbenchArtifacts
from llm_agent.application.workbench.contracts import PanelContent, PanelReference
from llm_agent.application.workbench.inventory import read_inventory
from llm_agent.application.workbench.submission import observe_workbench_settlement


class UnavailableApproval:
    def present(self, prompt: ApprovalPrompt) -> ApprovalResponse:
        return ApprovalResponse.UNAVAILABLE


class WorkbenchSession:
    def __init__(self, start: TaskExecutionStart) -> None:
        self.start = start
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="workbench")
        self._runtime: TaskExecutionRuntime | None = None
        self._artifacts: WorkbenchArtifacts | None = None
        self._cancel = Event()
        self._request_cancel: Callable[[], None] | None = None
        self._busy: Future[Any] | None = None
        self._closed = False
        self._close_lock = Lock()
        self._close_future: Future[None] | None = None

    def _ensure_runtime(self) -> TaskExecutionRuntime:
        if self._runtime is None:
            self._runtime = start_interactive_session(self.start, UnavailableApproval())
            context = read_execution_context(self._runtime)
            self._artifacts = WorkbenchArtifacts(context.workspace_paths)
        return self._runtime

    def submit(
        self,
        text: str,
        *,
        stream_callback: Callable[[str], None] | None = None,
    ) -> Future[tuple[InteractiveTaskObservation, PanelReference | None]]:
        self._check_idle()
        if not text.strip():
            raise ValueError("empty submission")
        self._cancel.clear()
        future = self._executor.submit(self._execute, text, stream_callback)
        self._busy = future
        return future

    def _check_idle(self) -> None:
        if self._closed or (self._busy is not None and not self._busy.done()):
            raise RuntimeError("workbench is closed or already processing a request")

    def _execute(
        self,
        text: str,
        stream_callback: Callable[[str], None] | None = None,
    ) -> tuple[InteractiveTaskObservation, PanelReference | None]:
        runtime = self._ensure_runtime()
        cleanup = bind_submission_cancellation(runtime, self._cancel, self._register_cancel)
        try:
            if self._cancel.is_set():
                raise RuntimeError("solicitação cancelada antes da execução")
            settlement = execute_submission(
                runtime, text, entry="natural", stream_callback=stream_callback,
            )
            observation = observe_workbench_settlement(settlement)
            assert self._artifacts is not None
            return observation, self._artifacts.offer_report(observation)
        finally:
            cleanup()
            self._request_cancel = None

    def _register_cancel(self, callback: Callable[[], None]) -> None:
        self._request_cancel = callback

    def cancel(self) -> None:
        self._cancel.set()
        callback = self._request_cancel
        if callback is not None:
            callback()

    def outputs(self) -> Future[tuple[PanelReference, ...]]:
        self._check_idle()
        def read() -> tuple[PanelReference, ...]:
            self._ensure_runtime()
            assert self._artifacts is not None
            return self._artifacts.list_outputs()
        future = self._executor.submit(read)
        self._busy = future
        return future

    def read(self, reference: PanelReference, offset: int = 0) -> Future[PanelContent]:
        self._check_idle()
        def load() -> PanelContent:
            assert self._artifacts is not None
            return self._artifacts.read(reference, offset=offset)
        future = self._executor.submit(load)
        self._busy = future
        return future

    def close(self) -> Future[None]:
        with self._close_lock:
            if self._close_future is not None:
                return self._close_future
            self._closed = True
            self.cancel()
            future = self._executor.submit(self._close_runtime)
            self._close_future = future
            self._executor.shutdown(wait=False)
            return future

    def inventory(self) -> Future[PanelContent]:
        self._check_idle()
        future = self._executor.submit(lambda: read_inventory(self._ensure_runtime()))
        self._busy = future
        return future

    def _close_runtime(self) -> None:
        if self._runtime is not None:
            close_task_execution(self._runtime)

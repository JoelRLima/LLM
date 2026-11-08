"""Small lifecycle adapter for bounded application shutdown."""

from __future__ import annotations

import threading
from typing import Any

from llm_agent.agent.application_cleanup import release_resources
from llm_agent.agent.application_shutdown import require_application_invocations_drained
from llm_agent.agent.observability.application_adapter import finish_observation

_RUN_LOCK = threading.RLock()


class ApplicationLifecycleMixin:
    def cancel(self: Any) -> None:
        if not self._closed and not self.interaction_service().cancel_active_model_call():
            self.orchestrator.cancel_task()

    def close(self: Any) -> None:
        close_application(self, _RUN_LOCK)

    def __enter__(self: Any) -> Any:
        return self

    def __exit__(self: Any, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()


def close_application(application: Any, run_lock: Any) -> None:
    """Drain invocations, finalize observation, and release application resources."""

    with run_lock:
        if application._closed:
            return
        primary_error: BaseException | None = None
        cleanup_error: BaseException | None = None
        drained = False
        try:
            require_application_invocations_drained(application.tool_invocation_gateway)
            drained = True
            if not application._task_attempted:
                application.orchestrator._persist_memory_to_file()
        except BaseException as exc:
            primary_error = exc
        finish_observation(application)
        if drained:
            cleanup_error = release_resources(
                application._instance_lock,
                application._owns_logging,
                application._home_lease,
            )
            application._closed = True
        if primary_error is not None:
            raise primary_error
        if cleanup_error is not None:
            raise cleanup_error


__all__ = ["ApplicationLifecycleMixin", "close_application"]

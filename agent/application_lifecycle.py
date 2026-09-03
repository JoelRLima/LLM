"""Small lifecycle adapter for bounded application shutdown."""

from __future__ import annotations

from typing import Any

from agent.application_cleanup import release_resources
from agent.application_shutdown import require_application_invocations_drained
from agent.observability.application_adapter import finish_observation


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
            cleanup_error = release_resources(application._instance_lock, application._owns_logging)
            application._closed = True
        if primary_error is not None:
            raise primary_error
        if cleanup_error is not None:
            raise cleanup_error


__all__ = ["close_application"]

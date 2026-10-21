"""Application use cases for session diagnostic mode."""

from __future__ import annotations

from llm_agent.agent.runtime.logging import set_debug_level as _set_debug_level
from llm_agent.application.task_execution import TaskExecutionRuntime, set_session_diagnostics

__all__ = ["apply_interactive_diagnostic_mode"]


def apply_interactive_diagnostic_mode(
    runtime: TaskExecutionRuntime,
    mode: int,
    *,
    session_diagnostics_enabled: bool | None,
) -> None:
    """Apply one interactive mode to the session when it owns verbosity and to canonical logging."""

    if mode not in {0, 1, 2}:
        raise ValueError("interactive diagnostic mode must be 0, 1, or 2")
    if session_diagnostics_enabled is not None:
        set_session_diagnostics(runtime, session_diagnostics_enabled)
    _set_debug_level(0 if mode == 0 else 1)

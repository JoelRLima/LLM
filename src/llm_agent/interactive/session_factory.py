"""Shared composition for local-first interactive sessions."""

from __future__ import annotations

from pathlib import Path

from llm_agent.application.context import AppPaths
from llm_agent.application.task_execution import TaskExecutionStart
from llm_agent.interactive.session import InteractiveSession


def create_local_session(
    *,
    workspace: str | Path | None = None,
    home: str | Path | None = None,
    config_path: str | Path | None = None,
    profile: str | None = None,
    observability_mode: str | None = None,
) -> tuple[InteractiveSession, Path]:
    """Create the same model-disabled session used by local Workbench flows."""

    root = Path(workspace or Path.cwd()).expanduser().resolve()
    start = TaskExecutionStart(
        workspace=root,
        app_paths=AppPaths.discover(app_home=home),
        config_path=config_path,
        profile=profile,
        observability_mode=observability_mode,
        configure_logging=False,
    )
    return InteractiveSession(start, llm_enabled=False), root


__all__ = ["create_local_session"]

from __future__ import annotations

from pathlib import Path

from llm_agent.application.task_execution import TaskExecutionStart
from llm_agent.interfaces.workbench.session import WorkbenchSession
from llm_agent.workspace.paths import AppPaths


def test_close_is_idempotent_and_returns_the_original_settlement() -> None:
    paths = AppPaths.discover(Path(".tmp/workbench-session-home"), env={})
    session = WorkbenchSession(TaskExecutionStart(Path.cwd(), paths, configure_logging=False))

    first = session.close()
    assert first.result(timeout=2) is None

    second = session.close()
    assert second is first
    assert second.result(timeout=2) is None

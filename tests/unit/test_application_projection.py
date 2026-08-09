from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.application import AgentApplication


@pytest.mark.parametrize(
    "status",
    ["failed", "blocked", "cancelled", "unverified", "timed_out"],
)
def test_application_preserves_projected_terminal_status(status: str) -> None:
    app = AgentApplication.__new__(AgentApplication)
    app._closed = False
    app._task_attempted = False
    app.workspace = SimpleNamespace(root=Path("."))
    app.orchestrator = SimpleNamespace(
        run=lambda _objective: "terminal answer",
        agent_state=SimpleNamespace(
            last_result={"ok": False, "done": True, "status": status, "error": status}
        ),
        _cancelled=False,
        _task_failed=False,
    )

    result = app._run_locked("objective")

    assert result.status == status
    assert result.status != "succeeded"

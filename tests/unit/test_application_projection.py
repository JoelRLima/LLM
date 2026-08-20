from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.application import AgentApplication


@pytest.mark.parametrize(
    "status",
    ["failed", "blocked", "cancelled", "unverified", "timed_out", "permission_denied", "protocol_error", "unavailable"],
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
    assert result.success is False
    assert result.receipt["status"] == status
    assert result.receipt["operational_outcome"]["terminal_status"] == status


def test_application_permission_denied_public_convergence(tmp_path: Path) -> None:
    app = AgentApplication.__new__(AgentApplication)
    app._closed = False
    app._task_attempted = False
    app.workspace = SimpleNamespace(root=tmp_path)
    app.orchestrator = SimpleNamespace(
        run=lambda _objective: "A tarefa foi negada.",
        agent_state=SimpleNamespace(
            last_result={
                "ok": False,
                "executed": False,
                "status": "permission_denied",
                "error_code": "TASK_AUTHORITY_DENIED",
                "error": "TASK_AUTHORITY_DENIED",
                "message": "Task authority insuficiente.",
            },
            tool_history=[
                {
                    "tool": "secure_tool",
                    "args": {},
                    "invocation_id": "inv-auth-1",
                    "result": {
                        "ok": False,
                        "executed": False,
                        "status": "permission_denied",
                        "error_code": "TASK_AUTHORITY_DENIED",
                        "error": "TASK_AUTHORITY_DENIED",
                        "invocation_id": "inv-auth-1",
                    },
                }
            ],
            terminal_disposition="permission_denied",
            step_records={},
            events=[],
            requested_effects=(),
            executed_effects=(),
            waived_effects=(),
            pending_effects=lambda: (),
        ),
        _cancelled=False,
        _task_failed=False,
    )

    result = app._run_locked("secure objective")

    assert result.status == "permission_denied"
    assert result.receipt["status"] == "permission_denied"
    assert result.receipt["operational_outcome"]["terminal_status"] == "permission_denied"
    assert result.success is False
    assert result.receipt["executed"] is False
    assert "inv-auth-1" in result.receipt["operational_outcome"]["evidence_invocation_ids"]

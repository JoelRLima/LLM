from types import SimpleNamespace

import pytest

from agent.application import AgentApplication
from agent.reporting.task_report import TaskReportBuilder
from agent.runtime.paths import REPORTS_DIR


def test_task_report_default_stays_under_the_canonical_runtime_directory() -> None:
    assert TaskReportBuilder({}).output_dir == REPORTS_DIR


def test_task_report_projects_invocation_and_output_bounds() -> None:
    report = TaskReportBuilder({}).build_report(
        type(
            "State",
            (),
            {
                "objective": "read",
                "tool_history": [
                    {
                        "tool": "file_reader",
                        "args": {"file_path": "notes.txt"},
                        "invocation_id": "inv-1",
                        "result": {
                            "invocation_id": "inv-1",
                            "ok": True,
                            "status": "succeeded",
                            "data": "hello",
                            "metadata": {"total_chars": 5, "truncated": False},
                        },
                    }
                ],
                "events": [],
                "last_result": {"ok": True},
            },
        )(),
        [],
        "hello",
        canonical_outcome={"status": "succeeded", "error": None},
    )

    step = report["steps"][0]
    assert step["invocation_id"] == "inv-1"
    assert step["result"]["status"] == "succeeded"
    assert step["result"]["output_chars"] == 5
    assert step["result"]["truncated"] is False


def test_task_report_does_not_infer_success_from_final_answer() -> None:
    state = type(
        "State",
        (),
        {
            "objective": "failed task",
            "tool_history": [
                {"tool": "shell", "args": {}, "result": {"ok": False, "status": "failed", "error": "boom"}}
            ],
            "events": [],
            "last_result": None,
        },
    )()

    report = TaskReportBuilder({}).build_report(
        state,
        [],
        "A resposta textual nao prova sucesso.",
        canonical_outcome={"status": "failed", "error": "boom"},
    )

    assert report["success"] is False


def test_task_report_requires_canonical_outcome() -> None:
    with pytest.raises(TypeError, match="canonical_outcome"):
        TaskReportBuilder({}).build_report(type("State", (), {})(), [], "claimed success")  # type: ignore[call-arg]


@pytest.mark.parametrize(
    ("status", "code"),
    (("permission_denied", "TASK_AUTHORITY_MISSING"), ("permission_denied", "WORKSPACE_GRANT_DENIED"), ("blocked", "APPROVAL_REQUIRED")),
)
def test_public_receipt_preserves_denial_cause_and_no_effect(tmp_path, status: str, code: str) -> None:
    raw_result = {
        "status": status,
        "ok": False,
        "error": "public-safe denial",
        "error_code": code,
        "invocation_id": "inv-denied",
    }
    app = object.__new__(AgentApplication)
    app.workspace = SimpleNamespace(root=tmp_path)
    app.orchestrator = SimpleNamespace(
        agent_state=SimpleNamespace(
            last_result=raw_result,
            tool_history=[
                {"tool": "demo_tool", "invocation_id": "inv-denied", "result": raw_result}
            ],
            events=[],
        ),
        _last_failure_code=None,
        _last_failure_layer=None,
        _generate_task_report=lambda *args, **kwargs: None,
    )

    result = app._result(status, "blocked", error="public-safe denial")

    assert result.receipt["error"]["code"] == code
    assert result.receipt["tools"][0]["invocation_id"] == "inv-denied"
    assert result.receipt["executed"] is False
    assert result.diagnostics[0]["code"] == code
    assert result.diagnostics[0]["layer"] == "gateway"
    assert result.diagnostics[0]["executed"] is False

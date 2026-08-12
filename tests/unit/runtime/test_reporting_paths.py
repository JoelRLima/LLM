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
        "executed": False,
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


def test_unknown_legacy_history_keeps_executed_unknown(tmp_path) -> None:
    raw = {"status": "succeeded", "ok": True, "data": "legacy"}
    app = object.__new__(AgentApplication)
    app.workspace = SimpleNamespace(root=tmp_path)
    app.orchestrator = SimpleNamespace(
        agent_state=SimpleNamespace(last_result=raw, tool_history=[{"tool": "legacy", "result": raw}], events=[]),
        _last_failure_code=None,
        _last_failure_layer=None,
        _generate_task_report=lambda *args, **kwargs: None,
    )

    result = app._result("succeeded", "legacy")

    assert result.receipt["tools"][0]["executed"] is None


def test_preview_artifact_does_not_claim_applied_files(tmp_path) -> None:
    raw = {
        "status": "blocked",
        "ok": False,
        "executed": False,
        "data": {
            "artifacts": [{"metadata": {"affected_files": ["module.py"], "applied": False}}]
        },
        "error_code": "APPROVAL_REQUIRED",
        "error": "approval required",
    }
    app = object.__new__(AgentApplication)
    app.workspace = SimpleNamespace(root=tmp_path)
    app.orchestrator = SimpleNamespace(
        agent_state=SimpleNamespace(last_result=raw, tool_history=[{"tool": "code_task", "result": raw}], events=[]),
        _last_failure_code=None,
        _last_failure_layer=None,
        _generate_task_report=lambda *args, **kwargs: None,
    )

    result = app._result("blocked", "preview", error="approval required")

    assert result.receipt["proposed_files"] == ["module.py"]
    assert result.receipt["files_affected"] == []
    assert result.receipt["final_state"] is None


@pytest.mark.parametrize("executed", [True, False, None])
def test_receipt_executed_preserves_tool_result_with_applied_artifact(tmp_path, executed) -> None:
    raw = {
        "status": "succeeded",
        "ok": True,
        "executed": executed,
        "data": {
            "artifacts": [{"metadata": {"affected_files": ["module.py"], "applied": True}}]
        },
    }
    app = object.__new__(AgentApplication)
    app.workspace = SimpleNamespace(root=tmp_path)
    app.orchestrator = SimpleNamespace(
        agent_state=SimpleNamespace(
            last_result=raw,
            tool_history=[{"tool": "code_task", "result": raw}],
            events=[],
        ),
        _last_failure_code=None,
        _last_failure_layer=None,
        _generate_task_report=lambda *args, **kwargs: None,
    )

    result = app._result("succeeded", "applied", error=None)

    assert result.receipt["tools"][0]["executed"] is executed
    assert result.receipt["executed"] is executed
    assert result.receipt["files_affected"] == ["module.py"]
    assert result.receipt["final_state"] == "applied"


def test_post_mortem_args_keep_only_bounded_resource_identity() -> None:
    secret = "TOPSECRET"
    report = TaskReportBuilder({}).build_report(
        SimpleNamespace(
            objective="read",
            events=[],
            tool_history=[{
                "tool": "file_reader",
                "args": {
                    "file_path": "README.md",
                    "api_key": secret,
                    "content": secret,
                    "nested": {"password": secret},
                },
                "result": {"ok": True, "status": "succeeded", "executed": True},
            }],
        ),
        [],
        "done",
        canonical_outcome={"status": "succeeded", "error": None},
    )

    assert report["steps"][0]["args"] == {"file_path": "README.md"}
    assert secret not in repr(report["steps"][0]["args"])

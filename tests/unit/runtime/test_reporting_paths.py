from types import SimpleNamespace

import pytest

from agent.application import AgentApplication
from agent.llm.contracts import TokenUsage
from agent.reporting.operational_outcome import normalize_terminal_status
from agent.reporting.run_receipt import build_run_diagnostics, derive_status, public_exception_message
from agent.reporting.task_report import TaskReportBuilder
from agent.reporting.task_report_rendering import render_markdown
from agent.runtime.budget import BudgetExhausted, TaskBudgetLedger
from agent.runtime.paths import REPORTS_DIR
from agent.state import AgentState


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


def test_task_report_distinguishes_real_tools_from_history_records() -> None:
    ledger = TaskBudgetLedger(max_task_tool_calls=3)
    ledger.reserve_tool_call()
    state = SimpleNamespace(
        objective="read",
        budget_ledger=ledger,
        tool_history=[{"tool": "synthetic"}, {"tool": "file_reader"}],
        events=[],
        last_result=None,
    )

    report = TaskReportBuilder({}).build_report(
        state,
        [],
        "done",
        canonical_outcome={"status": "succeeded", "error": None},
    )

    assert report["metrics"]["tool_calls"] == 1
    assert report["metrics"]["tools_called"] == 1
    assert report["metrics"]["history_records"] == 2


def test_task_report_preserves_total_only_ledger_usage_without_metric_rows() -> None:
    ledger = TaskBudgetLedger()
    call_number = ledger.reserve_model_call()
    ledger.finalize_model_call(call_number, usage=TokenUsage(total_tokens=15))
    state = SimpleNamespace(
        objective="report",
        budget_ledger=ledger,
        tool_history=[],
        events=[],
        last_result=None,
    )

    report = TaskReportBuilder({}).build_report(
        state,
        [],
        "done",
        canonical_outcome={"status": "succeeded", "error": None},
    )

    assert report["metrics"]["total_tokens"] == 15
    assert report["metrics"]["reported_total_tokens"] == 15
    assert report["metrics"]["accounted_tokens"] == 15
    assert report["metrics"]["token_usage_complete"] is True


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


@pytest.mark.parametrize(
    ("disposition", "expected"),
    (("block", "blocked"), ("fail", "failed")),
)
def test_public_status_honors_canonical_terminal_disposition(
    disposition: str, expected: str
) -> None:
    orchestrator = SimpleNamespace(
        agent_state=SimpleNamespace(
            last_result={"ok": True, "status": "succeeded"},
            terminal_disposition=disposition,
        ),
        _cancelled=False,
        _task_failed=False,
    )

    assert derive_status(orchestrator) == expected


def test_public_result_cannot_promote_canonical_block_to_success(tmp_path) -> None:
    raw = {
        "ok": False,
        "done": True,
        "status": "blocked",
        "error": "approval required",
        "error_code": "APPROVAL_REQUIRED",
        "executed": False,
    }
    app = object.__new__(AgentApplication)
    app.workspace = SimpleNamespace(root=tmp_path)
    app.orchestrator = SimpleNamespace(
        agent_state=SimpleNamespace(
            terminal_disposition="block",
            last_result=raw,
            tool_history=[],
            events=[],
        ),
        _cancelled=False,
        _task_failed=False,
        _last_failure_code=None,
        _last_failure_layer=None,
        _generate_task_report=lambda *args, **kwargs: None,
    )

    result = app._result("succeeded", "claimed success")

    assert result.status == "blocked"
    assert result.success is False
    assert result.receipt["status"] == "blocked"
    assert result.receipt["operational_outcome"]["terminal_status"] == "blocked"


def test_application_budget_exception_is_blocked_and_preserves_reason(tmp_path) -> None:
    state = AgentState()
    events = []

    def raise_budget(_objective):
        raise BudgetExhausted("model_calls", 1, 1)

    orchestrator = SimpleNamespace(
        run=raise_budget,
        agent_state=state,
        _cancelled=False,
        _task_failed=False,
        _last_failure_code=None,
        _last_failure_layer=None,
        _emit=lambda event_type, data=None: events.append(
            {"type": event_type, "data": data or {}}
        ),
        _generate_task_report=lambda *args, **kwargs: None,
    )
    app = object.__new__(AgentApplication)
    app._closed = False
    app._task_attempted = False
    app.workspace = SimpleNamespace(root=tmp_path)
    app.orchestrator = orchestrator

    result = app._run_locked("objective")

    assert result.status == "blocked"
    assert result.success is False
    assert result.receipt["status"] == "blocked"
    assert result.receipt["operational_outcome"]["terminal_status"] == "blocked"
    assert result.receipt["error"]["code"] == "TASK_BUDGET_EXHAUSTED"
    assert any(event["type"] == "task_outcome" for event in events)


def test_run_metric_uses_canonical_status_before_finalization(tmp_path) -> None:
    recorded = []
    state = SimpleNamespace(
        terminal_disposition="block",
        last_result={"status": "blocked", "error": "blocked"},
        tool_history=[],
        events=[],
    )
    orchestrator = SimpleNamespace(
        agent_state=state,
        _cancelled=False,
        _task_failed=False,
        _record_canonical_run_metric=lambda success: recorded.append(success),
        _generate_task_report=lambda *args, **kwargs: None,
    )
    app = object.__new__(AgentApplication)
    app.workspace = SimpleNamespace(root=tmp_path)
    app.orchestrator = orchestrator

    result = app._result("succeeded", "claimed success")

    assert result.status == "blocked"
    assert recorded == [False]


def test_report_receipt_status_parity_fails_closed_on_mismatch() -> None:
    report = TaskReportBuilder({}).build_report(
        SimpleNamespace(objective="objective", tool_history=[], events=[], last_result=None),
        [],
        "blocked",
        canonical_outcome={"status": "blocked", "error": "denied"},
        receipt={
            "status": "succeeded",
            "operational_outcome": {"terminal_status": "succeeded"},
        },
    )

    assert report["status"] == "blocked"
    assert report["receipt"]["status"] == "blocked"
    assert report["operational_outcome"]["terminal_status"] == "blocked"


def test_exception_diagnostics_redact_common_secret_forms() -> None:
    secret = "api_key=TOPSECRET Authorization: Bearer TOPSECRET password=TOPSECRET"
    message = public_exception_message(RuntimeError(secret))
    diagnostics = build_run_diagnostics(
        SimpleNamespace(
            last_result={"status": "failed", "error_code": "RUNTIME_ERROR", "error": secret}
        ),
        None,
    )

    assert "TOPSECRET" not in message
    assert "TOPSECRET" not in repr(diagnostics)


def test_task_report_redacts_tool_and_answer_secret_forms() -> None:
    secret = "api_key=TOPSECRET Authorization: Bearer TOPSECRET password=TOPSECRET"
    report = TaskReportBuilder({}).build_report(
        SimpleNamespace(
            objective="read",
            tool_history=[
                {
                    "tool": "demo",
                    "result": {
                        "ok": False,
                        "status": "failed",
                        "error": secret,
                        "data": {"details": secret},
                    },
                }
            ],
            events=[],
            last_result={"status": "failed"},
        ),
        [],
        secret,
        canonical_outcome={"status": "failed", "error": secret},
    )

    assert "TOPSECRET" not in repr(report)


def test_markdown_report_keeps_operational_status_and_reason_code() -> None:
    report = TaskReportBuilder({}).build_report(
        SimpleNamespace(objective="audit", tool_history=[], events=[], last_result=None),
        [],
        "blocked",
        canonical_outcome={"status": "permission_denied", "error": "denied"},
        receipt={"error": {"code": "TASK_AUTHORITY_DENIED"}},
    )

    markdown = render_markdown(report)

    assert "permission_denied" in markdown
    assert "TASK_AUTHORITY_DENIED" in markdown


@pytest.mark.parametrize(
    ("facts", "expected"),
    (
        ({"terminal_disposition": "complete", "task_failed": True}, "failed"),
        ({"terminal_disposition": "complete", "last_result_status": "failed"}, "failed"),
        ({"terminal_disposition": "block", "last_result_status": "failed"}, "failed"),
        ({"terminal_disposition": "complete", "last_result_status": "succeeded"}, "succeeded"),
        ({"terminal_disposition": "block", "last_result_status": "succeeded"}, "blocked"),
        ({"terminal_disposition": "complete", "cancelled": True}, "cancelled"),
    ),
)
def test_terminal_status_normalization_has_one_lifecycle_precedence(facts, expected) -> None:
    assert normalize_terminal_status(**facts) == expected


def test_post_completion_failure_projects_failed_everywhere(tmp_path) -> None:
    state = SimpleNamespace(
        terminal_disposition="complete",
        last_result={"ok": True, "status": "succeeded"},
        tool_history=[],
        events=[],
    )

    def fail_after_completion(_objective):
        state.terminal_disposition = "complete"
        raise RuntimeError("cleanup failed")

    app = AgentApplication.__new__(AgentApplication)
    app._closed = False
    app._task_attempted = False
    app.workspace = SimpleNamespace(root=tmp_path)
    app.orchestrator = SimpleNamespace(
        run=fail_after_completion,
        agent_state=state,
        _cancelled=False,
        _task_failed=False,
        _last_failure_code=None,
        _last_failure_layer=None,
    )

    result = app._run_locked("objective")

    assert result.status == "failed"
    assert result.receipt["status"] == "failed"
    assert result.receipt["operational_outcome"]["terminal_status"] == "failed"


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


def test_preview_artifact_with_staged_mutation_is_only_proposed(tmp_path) -> None:
    raw = {
        "status": "blocked",
        "ok": False,
        "executed": True,
        "data": {
            "artifacts": [{"metadata": {
                "affected_files": ["module.py"],
                "applied": False,
                "mutation_occurred": True,
            }}]
        },
        "error_code": "TOOL_ERROR",
        "error": "confirmation_required",
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

    result = app._result("blocked", "preview", error="confirmation_required")

    assert result.receipt["proposed_files"] == ["module.py"]
    assert result.receipt["files_affected"] == []
    assert result.receipt["mutation_occurred"] is False


@pytest.mark.parametrize("executed", [True, False, None])
def test_receipt_executed_preserves_tool_result_with_applied_artifact(tmp_path, executed) -> None:
    raw = {
        "status": "succeeded",
        "ok": True,
        "executed": executed,
        "data": {
            "artifacts": [{"metadata": {
                "affected_files": ["module.py"],
                "applied": True,
                "mutation_occurred": True,
                "final_state": "applied",
            }}]
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
    assert result.receipt["mutation_occurred"] is True


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

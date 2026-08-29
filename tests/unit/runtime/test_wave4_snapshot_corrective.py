import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from agent.evaluation.agent_executor import snapshot_evaluation_projection
from agent.evaluation.execution_evidence import h2_reporting
from agent.planning.plan_model import Plan
from agent.reporting.metrics import project_run_metrics
from agent.reporting.public_projection import canonical_effect_projection
from agent.reporting.run_receipt import build_run_receipt
from agent.reporting.run_snapshot import build_canonical_run_snapshot
from agent.reporting.task_report import TaskReportBuilder
from agent.runtime.correlation import RunCorrelation
from agent.runtime.failure_policy import local_failure_permitted
from agent.runtime.outcome_taxonomy import FailureLayer
from agent.state import AgentState
from agent.tools.contracts import ToolError, ToolResult, ToolStatus


def _owner(state: object, correlation: RunCorrelation, **values: object) -> SimpleNamespace:
    return SimpleNamespace(
        agent_state=state,
        _run_correlation=correlation,
        _task_failed=values.get("task_failed", False),
        _cancelled=values.get("cancelled", False),
        _last_failure_code=values.get("failure_code"),
        _last_failure_layer=values.get("failure_layer"),
    )


@pytest.mark.parametrize(
    ("requested", "disposition", "cancelled", "expected"),
    (
        ("succeeded", "complete", False, "succeeded"),
        ("blocked", "block", False, "blocked"),
        ("failed", "fail", False, "failed"),
        ("cancelled", "cancelled", True, "cancelled"),
        ("unverified", None, False, "unverified"),
    ),
)
def test_explicit_snapshot_terminal_matrix(
    requested: str,
    disposition: str | None,
    cancelled: bool,
    expected: str,
) -> None:
    state = AgentState()
    state.terminal_disposition = disposition
    correlation = RunCorrelation.fresh()
    metrics = project_run_metrics([])

    snapshot = build_canonical_run_snapshot(
        _owner(state, correlation, cancelled=cancelled),
        requested,
        metrics=metrics,
    )

    assert snapshot.status == expected
    assert snapshot.operational_outcome.terminal_status == expected
    assert snapshot.metrics is metrics


def test_local_failure_permitted_projects_unverified_without_false_failure() -> None:
    failed = ToolResult(
        "local-failure",
        ToolStatus.FAILED,
        error=ToolError("FILE_NOT_FOUND", "missing"),
        executed=False,
    )
    state = SimpleNamespace(
        objective="fallback",
        last_result=failed,
        last_tool="file_reader",
        current_step_id="step-1",
        tool_history=[{"tool": "file_reader", "result": failed}],
        events=[],
        execution_incidents=[],
        terminal_disposition=None,
        task_semantics=SimpleNamespace(failure_observation_permitted=lambda _ref: True),
        _later_recovery=lambda *_args: False,
        requested_effects=(),
        executed_effects=(),
        waived_effects=(),
        pending_effects=(),
        _task_failed=False,
        _cancelled=False,
    )

    snapshot = build_canonical_run_snapshot(
        _owner(state, RunCorrelation.fresh()),
        "unverified",
        metrics=project_run_metrics([]),
    )

    assert snapshot.status == "unverified"
    assert local_failure_permitted(state) is True


def test_failure_code_layer_outcome_and_metrics_are_reused_by_final_projections() -> None:
    failed = ToolResult(
        "inv-denied",
        ToolStatus.PERMISSION_DENIED,
        error=ToolError("TASK_AUTHORITY_DENIED", "denied"),
        executed=False,
    )
    state = AgentState()
    state.last_result = failed
    state.last_tool = "writer"
    state.current_step_id = "step-denied"
    state.tool_history = [{"tool": "writer", "result": failed}]
    state.terminal_disposition = "fail"
    metrics = project_run_metrics(
        [{"type": "model_call", "total_tokens": 13, "token_usage_complete": True}]
    )
    correlation = RunCorrelation.fresh()
    snapshot = build_canonical_run_snapshot(
        _owner(state, correlation),
        "failed",
        error="denied",
        metrics=metrics,
    )
    receipt = build_run_receipt(".", state, "succeeded", None, snapshot=snapshot)
    report = TaskReportBuilder({}).build_report(
        state, [], "denied", snapshot=snapshot, receipt=receipt
    )

    assert snapshot.failure_fact is not None
    assert snapshot.failure_fact.code == "TASK_AUTHORITY_DENIED"
    assert snapshot.failure_fact.layer is FailureLayer.GATEWAY
    assert receipt["failure_fact"] == snapshot.failure_fact.to_dict()
    assert receipt["operational_outcome"] == snapshot.operational_outcome.to_dict()
    assert report["operational_outcome"] == snapshot.operational_outcome.to_dict()
    assert receipt["metrics"] == metrics.to_dict()
    assert report["metrics"] == metrics.to_dict()
    assert canonical_effect_projection(state, snapshot.status, snapshot=snapshot)[
        "operational_outcome"
    ] == snapshot.operational_outcome.to_dict()


def _mutation_state() -> AgentState:
    state = AgentState()
    state.objective = "apply bounded change"
    applied = {
        "status": "succeeded",
        "ok": True,
        "executed": True,
        "invocation_id": "inv-applied",
        "data": {
            "value": "api_key=SYNTHETIC_SECRET " + "x" * 2000,
            "artifacts": [
                {
                    "metadata": {
                        "affected_files": ["applied.py"],
                        "applied": True,
                        "mutation_occurred": True,
                        "final_state": "applied",
                        "validation_status": "passed",
                    }
                }
            ],
        },
    }
    proposed = {
        "status": "blocked",
        "ok": False,
        "executed": False,
        "invocation_id": "inv-proposed",
        "error": "approval required",
        "error_code": "APPROVAL_REQUIRED",
        "data": {
            "artifacts": [
                {
                    "metadata": {
                        "affected_files": ["proposed.py"],
                        "applied": False,
                    }
                }
            ]
        },
    }
    state.tool_history = [
        {"tool": "code_task", "args": {"action": "repair"}, "result": applied},
        {"tool": "code_task", "args": {"action": "preview"}, "result": proposed},
    ]
    state.last_result = proposed
    state.last_tool = "code_task"
    state.events = [
        {"type": "plan_created", "data": {"steps": 2}},
        {
            "type": "replan",
            "data": {
                "original_step": 1,
                "error": "retry",
                "strategy": "repair",
                "replacement_steps": 1,
            },
        },
    ]
    state.execution_incidents = [
        {
            "incident_type": "CANONICAL_COMMIT_FAILED",
            "invocation_id": "inv-incident",
            "tool": "code_task",
            "original_tool_status": "succeeded",
            "executed": True,
            "effect_state": "PROVEN",
            "affected_files": ["incident.py"],
            "rollback_occurred": True,
            "error_code": "CANONICAL_COMMIT_FAILED",
        }
    ]
    state.terminal_disposition = "block"
    return state


def _without_report_identity(report: dict[str, object]) -> dict[str, object]:
    projected = deepcopy(report)
    projected.pop("report_id", None)
    return projected


def test_snapshot_present_receipt_and_report_ignore_all_later_state_mutation() -> None:
    state = _mutation_state()
    correlation = RunCorrelation.fresh()
    snapshot = build_canonical_run_snapshot(
        _owner(state, correlation),
        "blocked",
        error="approval required",
        metrics=project_run_metrics(
            [{"type": "model_call", "total_tokens": 21, "token_usage_complete": True}]
        ),
    )
    receipt_a = build_run_receipt(".", state, "failed", "changed", snapshot=snapshot)
    report_a = TaskReportBuilder({}).build_report(
        state, [], "stable answer", snapshot=snapshot, receipt=receipt_a
    )
    frozen_evaluation = snapshot.projection_facts.to_dict()
    evaluation_a = snapshot_evaluation_projection(snapshot)

    state.tool_history = [{"tool": "forged", "result": {"status": "succeeded"}}]
    state.events = [{"type": "direct_response", "data": {}}]
    state.last_result = {"status": "succeeded", "data": {"forged": True}}
    state.execution_incidents = []
    state.objective = "forged objective"
    state.terminal_disposition = "complete"

    receipt_b = build_run_receipt(".", state, "succeeded", None, snapshot=snapshot)
    report_b = TaskReportBuilder({}).build_report(
        state, [], "stable answer", snapshot=snapshot, receipt=receipt_b
    )

    assert receipt_b == receipt_a
    assert _without_report_identity(report_b) == _without_report_identity(report_a)
    assert snapshot.projection_facts.to_dict() == frozen_evaluation
    assert snapshot_evaluation_projection(snapshot) == evaluation_a
    assert receipt_b["status"] == snapshot.status
    assert receipt_b["files_affected"] == list(snapshot.operational_outcome.files_affected)
    assert receipt_b["proposed_files"] == ["proposed.py"]
    assert receipt_b["validation"] == {"ran": True, "outcome": "passed"}
    assert receipt_b["rollback"]["occurred"] is snapshot.operational_outcome.rollback_occurred
    assert receipt_b["repair"] == {"occurred": True, "count": 1}
    assert receipt_b["replan"] == {"occurred": True, "count": 1}
    assert "SYNTHETIC_SECRET" not in repr(report_b)
    assert len(report_b["steps"][0]["result"]["data_summary"]) <= 503


def test_snapshot_evaluation_args_preserve_bindings_without_leaking_or_growing() -> None:
    state = AgentState()
    state.objective = "find the H2 marker"
    state.plan = Plan.from_raw(
        [
            {
                "tool": "grep",
                "args": {"path": "."},
                "bindings": {"pattern": {"from_step": 1, "path": []}},
                "_step_id": "step-grep",
            }
        ]
    )
    state.tool_history = [
        {
            "tool": "grep",
            "invocation_id": "inv-grep",
            "args": {
                "pattern": "H2_MARKER",
                "path": ".",
                "recursive": True,
                "max_results": 20,
                "secret": "LEAKED_SECRET",
                "payload": "x" * 20_000,
            },
            "result": {
                "status": "succeeded",
                "ok": True,
                "executed": True,
                "invocation_id": "inv-grep",
                "data": [],
            },
        }
    ]
    snapshot = build_canonical_run_snapshot(
        _owner(state, RunCorrelation.fresh()),
        "succeeded",
        metrics=project_run_metrics([]),
    )

    projection = snapshot_evaluation_projection(snapshot)
    invocation = projection["history"][0]
    args = invocation["args"]
    assert args["pattern"] == "H2_MARKER"
    assert args["path"] == "."
    assert args["recursive"] is True
    assert "LEAKED_SECRET" not in repr(args)
    assert len(json.dumps(args, ensure_ascii=False, sort_keys=True)) <= 8192

    evaluation_evidence = {
        **projection,
        "invocation_evidence": projection["history"],
    }
    report = SimpleNamespace(
        observation=SimpleNamespace(evidence=evaluation_evidence)
    )
    h2 = h2_reporting(report, {})
    assert h2["final_grep_args"]["pattern"] == "H2_MARKER"

from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest

from agent.application_result import AgentRunResult
from agent.reporting.metrics import project_run_metrics
from agent.reporting.run_receipt import build_run_receipt
from agent.reporting.run_snapshot import build_canonical_run_snapshot
from agent.reporting.task_report import TaskReportBuilder
from agent.runtime.correlation import RunCorrelation
from agent.runtime.event_dispatch import RuntimeEventDispatcher
from agent.runtime.events import MAX_EVENT_DATA_CHARS, RuntimeEvent, RuntimeEventKind
from agent.runtime.failures import FailureFact
from agent.state import AgentState
from agent.tools.contracts import ToolError, ToolResult, ToolStatus
from scripts.check_wave4_architecture import check_source, run_checks


def _state(last_result: object = None) -> SimpleNamespace:
    return SimpleNamespace(
        objective="wave4",
        last_result=last_result,
        last_tool="file_reader",
        current_step_id="step-1",
        tool_history=[],
        events=[],
        execution_incidents=[],
        requested_effects=(),
        executed_effects=(),
        waived_effects=(),
        pending_effects=(),
        terminal_disposition="complete" if last_result is None else "fail",
        _task_failed=False,
        _cancelled=False,
    )


def test_run_correlation_preserves_domains_across_fresh_resume_and_children() -> None:
    fresh = RunCorrelation.fresh()
    child = fresh.child("node-a")
    unrelated = fresh.unrelated_task()
    resumed = RunCorrelation.resume(fresh.root_task_id)

    assert fresh.run_id != fresh.root_task_id
    assert child.run_id == fresh.run_id
    assert child.root_task_id == fresh.root_task_id
    assert child.task_id not in {fresh.task_id, fresh.root_task_id}
    assert child.parent_task_id == fresh.task_id
    assert child.node_id == "node-a"
    assert unrelated.root_task_id != fresh.root_task_id
    assert resumed.run_id != fresh.run_id
    assert resumed.root_task_id == fresh.root_task_id
    assert resumed.task_id == fresh.root_task_id


def test_dispatcher_fans_out_the_same_immutable_event_and_observes_checkpoint_once() -> None:
    first: list[RuntimeEvent] = []
    second: list[RuntimeEvent] = []
    checkpoints: list[RuntimeEvent] = []
    correlation = RunCorrelation.fresh()
    event = RuntimeEvent.from_fields(
        RuntimeEventKind.STEP_COMPLETED,
        correlation,
        {"plan_id": "plan-1", "step_id": "step-1", "invocation_id": "inv-1"},
        plan_id="plan-1",
        step_id="step-1",
        invocation_id="inv-1",
    )
    dispatcher = RuntimeEventDispatcher(
        [first.append, second.append],
        checkpoint_observer=checkpoints.append,
    )

    dispatcher.emit(event)

    assert first == [event]
    assert second == [event]
    assert first[0] is second[0] is checkpoints[0] is event
    assert event.run_id == correlation.run_id
    assert event.root_task_id == correlation.root_task_id
    assert event.plan_id == "plan-1"
    assert event.step_id == "step-1"
    assert event.invocation_id == "inv-1"


def test_runtime_event_payload_is_bounded_and_taxonomy_is_fail_closed() -> None:
    event = RuntimeEvent.from_fields(
        "warning",
        RunCorrelation.fresh(),
        {"large": "x" * (MAX_EVENT_DATA_CHARS * 2), "unsupported": object()},
    )
    serialized = event.to_legacy_dict()

    assert len(str(serialized["data"])) <= MAX_EVENT_DATA_CHARS + 256
    assert serialized["data"]
    with pytest.raises(ValueError, match="unsupported runtime event kind"):
        RuntimeEvent.from_fields("not_a_runtime_event", RunCorrelation.fresh())


def test_checkpoint_round_trip_preserves_root_task_identity() -> None:
    state = AgentState(root_task_id="root-1")
    checkpoint = state.to_checkpoint_dict()
    restored = AgentState()

    restored.from_checkpoint_dict(checkpoint)

    assert checkpoint["root_task_id"] == "root-1"
    assert restored.root_task_id == "root-1"


def test_snapshot_is_final_fact_owner_and_is_created_once() -> None:
    failed_result = ToolResult(
        invocation_id="inv-1",
        status=ToolStatus.FAILED,
        error=ToolError("TASK_AUTHORITY_DENIED", "denied"),
        executed=False,
    )
    state = _state(failed_result)
    correlation = RunCorrelation.fresh()
    metrics = project_run_metrics([])
    calls: list[bool] = []
    orchestrator = SimpleNamespace(
        agent_state=state,
        _run_correlation=correlation,
        _task_failed=False,
        _cancelled=False,
        _last_failure_code=None,
        _last_failure_layer=None,
    )

    snapshot = build_canonical_run_snapshot(
        orchestrator,
        "failed",
        error="denied",
        metrics=metrics,
        record_metric=calls.append,
    )
    second = build_canonical_run_snapshot(
        orchestrator,
        "succeeded",
        metrics=project_run_metrics(
            [{"type": "model_call", "total_tokens": 999, "token_usage_complete": True}]
        ),
        record_metric=calls.append,
    )

    assert second is snapshot
    assert calls == [False]
    assert snapshot.status == "failed"
    assert snapshot.correlation == correlation
    assert snapshot.metrics is metrics
    assert snapshot.failure_fact is not None
    assert snapshot.failure_fact.code == "TASK_AUTHORITY_DENIED"
    assert isinstance(snapshot.failure_fact, FailureFact)
    with pytest.raises(FrozenInstanceError):
        snapshot.status = "succeeded"  # type: ignore[misc]


def test_receipt_and_report_project_snapshot_facts_without_recomputing_them() -> None:
    correlation = RunCorrelation.fresh()
    snapshot = build_canonical_run_snapshot(
        SimpleNamespace(
            agent_state=_state(),
            _run_correlation=correlation,
            _task_failed=False,
            _cancelled=False,
        ),
        "succeeded",
        metrics=project_run_metrics(
            [{"type": "model_call", "total_tokens": 7, "token_usage_complete": True}]
        ),
    )
    state = _state()
    state.terminal_disposition = "fail"
    receipt = build_run_receipt(".", state, "failed", "changed", snapshot=snapshot)
    report = TaskReportBuilder({}).build_report(
        state,
        [],
        "done",
        snapshot=snapshot,
        receipt=receipt,
    )

    assert receipt["status"] == snapshot.status
    assert receipt["operational_outcome"] == snapshot.operational_outcome.to_dict()
    assert receipt["metrics"] == snapshot.metrics.to_dict()
    assert report["status"] == snapshot.status
    assert report["metrics"] == snapshot.metrics.to_dict()
    assert report["operational_outcome"] == snapshot.operational_outcome.to_dict()
    assert report["run_id"] == correlation.run_id
    assert report["root_task_id"] == correlation.root_task_id
    assert report["task_id"] == correlation.task_id
    assert report["report_id"] not in {
        correlation.run_id,
        correlation.root_task_id,
        correlation.task_id,
    }


def test_wave4_architecture_checker_passes_and_rejects_adversarial_ownership() -> None:
    assert run_checks() == []
    assert any(item.startswith("W4-S1:") for item in check_source(
        "from uuid import uuid4\ndef bad(): return uuid4().hex",
        "agent/evaluation/adversarial.py",
    ))
    assert any(item.startswith("W4-S2:") for item in check_source(
        "def bad(state): state.events.append({'type': 'x', 'data': {}})",
        "agent/orchestration/adversarial.py",
    ))
    assert any(item.startswith("W4-S3:") for item in check_source(
        "def bad(entries): return next(item['run_id'] for item in entries)",
        "agent/reporting/adversarial.py",
    ))
    assert any(item.startswith("W4-S4:") for item in check_source(
        "from agent.runtime.operational_outcome import normalize_terminal_status\ndef bad(state): return normalize_terminal_status(explicit_status='succeeded')",
        "agent/reporting/adversarial.py",
    ))
    assert any(item.startswith("W4-S5:") for item in check_source(
        "from agent.reporting.metrics import project_run_metrics\ndef bad(entries): return project_run_metrics(entries)",
        "agent/evaluation/adversarial.py",
    ))
    assert any(item.startswith("W4-S6:") for item in check_source(
        "class TaskReportBuilder:\n    def _generate_task_id(self): return 'x'",
        "agent/reporting/adversarial.py",
    ))


def test_agent_run_result_success_is_snapshot_projection() -> None:
    correlation = RunCorrelation.fresh()
    snapshot = build_canonical_run_snapshot(
        SimpleNamespace(
            agent_state=_state(),
            _run_correlation=correlation,
            _task_failed=False,
            _cancelled=False,
        ),
        "succeeded",
        metrics=project_run_metrics([]),
    )
    result = AgentRunResult(
        status="succeeded",
        answer="done",
        workspace=".",
        snapshot=snapshot,
    )

    assert result.success is True
    assert result.ok is True

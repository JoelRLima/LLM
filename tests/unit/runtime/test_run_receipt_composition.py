"""Receipt composition keeps the canonical reporting behavior and seams."""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from llm_agent.agent import application_result, final_response
from llm_agent.agent.reporting import partial_response, run_receipt
from llm_agent.agent.reporting.metrics import project_run_metrics
from llm_agent.agent.reporting.run_projection_facts import thaw_projection
from llm_agent.agent.reporting.run_snapshot import CanonicalRunSnapshot, build_canonical_run_snapshot
from llm_agent.agent.runtime.correlation import RunCorrelation
from llm_agent.agent.runtime.operational_outcome import OperationalOutcome, project_operational_outcome
from llm_agent.agent.state import AgentState


def _outcome(status: str = "succeeded", **changes: Any) -> OperationalOutcome:
    values: dict[str, Any] = dict(
        terminal_status=status, requested_effects=(), executed_effects=(),
        waived_effects=(), pending_effects=(), mutation_occurred=False,
        validation_status=None, rollback_occurred=False, blocked_reason=None,
        failure_reason=None, files_affected=(), evidence_invocation_ids=(),
    )
    values.update(changes)
    return OperationalOutcome(**values)


def _history(value: Any = "observed-control", **changes: Any) -> list[dict[str, Any]]:
    result = dict(ok=True, status="succeeded", executed=True, data=value, complete=True)
    result.update(changes)
    if changes.get("missing"):
        result.pop("data")
    return [dict(tool="file_reader", invocation_id="read-control",
                 args={"file_path": "control.txt"}, result=result)]


def _owner(history: list[dict[str, Any]] | None = None) -> SimpleNamespace:
    state = AgentState()
    state.tool_history = history or []
    state.terminal_disposition = "complete"
    return SimpleNamespace(agent_state=state, _run_correlation=RunCorrelation.fresh(),
                           _task_failed=False, _cancelled=False, tool_registry=None)


def _snapshot(owner: Any, outcome: OperationalOutcome) -> CanonicalRunSnapshot:
    # Real frozen projection facts; supplied canonical outcomes isolate composition branches.
    snapshot = build_canonical_run_snapshot(owner, "succeeded", metrics=project_run_metrics([]))
    return replace(snapshot, status=outcome.terminal_status, operational_outcome=outcome)


def _finalize(owner: Any, snapshot: CanonicalRunSnapshot | None, answer: str = "safe prose",
              **kwargs: Any) -> Any:
    return run_receipt.finalize_run_result(
        application_result.AgentRunResult, "offline-workspace", owner,
        snapshot.status if snapshot else "succeeded", answer, snapshot=snapshot, **kwargs,
    )


@pytest.mark.parametrize(("outcome", "expected"), [
    (_outcome(), "safe prose"),
    (_outcome(requested_effects=("write",), executed_effects=("write",),
              mutation_occurred=True, files_affected=("control.txt",), validation_status="passed"),
     "Uma altera\u00e7\u00e3o foi aplicada. Arquivos afetados: control.txt. Valida\u00e7\u00e3o: passed."),
    (_outcome(requested_effects=("write",), waived_effects=("write",)),
     "Nenhuma escrita foi executada."),
    (_outcome(requested_effects=("write",), pending_effects=("write",)), "permanece pendente"),
    (_outcome("failed", rollback_occurred=True, rollback_succeeded=True), "nenhuma escrita persistiu"),
    (_outcome("failed", rollback_occurred=True, rollback_succeeded=False,
              surviving_files=("control.txt",)), "control.txt"),
    (_outcome(failed_invocation_ids=("read-failed",), failed_invocation_statuses=("failed",),
              recovered_invocation_ids=("read-failed",)), "read-failed (failed)"),
])
def test_receipt_success_effect_rollback_and_recovery_truth(outcome: OperationalOutcome, expected: str) -> None:
    owner = _owner()
    snapshot = _snapshot(owner, outcome)
    result = _finalize(owner, snapshot)
    assert expected in result.answer
    assert result.answer == final_response.compose_operational_answer(outcome, "safe prose", [], None)
    assert result.status == outcome.terminal_status
    assert result.receipt["operational_outcome"] == outcome.to_dict()
    assert result.snapshot is snapshot


@pytest.mark.parametrize("status", ["failed", "unavailable", "timed_out", "blocked", "permission_denied", "cancelled"])
def test_no_evidence_preserves_deterministic_terminal_truth(status: str) -> None:
    owner = _owner()
    outcome = _outcome(status)
    result = _finalize(owner, _snapshot(owner, outcome), "The task completed successfully")
    assert result.answer == partial_response.render_operational_answer(outcome)
    assert "completed successfully" not in result.answer
    assert result.status == status


@pytest.mark.parametrize("reason", ["permission denied", "approval required", "authority denied"])
def test_blocked_denial_never_softens_with_partial_evidence(reason: str) -> None:
    owner = _owner(_history())
    outcome = _outcome("blocked", blocked_reason=reason)
    result = _finalize(owner, _snapshot(owner, outcome))
    assert result.answer == partial_response.render_operational_answer(outcome)
    assert "observed-control" not in result.answer
    assert "safe prose" not in result.answer


def test_cancellation_never_softens_with_partial_evidence() -> None:
    owner = _owner(_history())
    outcome = _outcome("cancelled")
    result = _finalize(owner, _snapshot(owner, outcome))
    assert result.answer == partial_response.render_operational_answer(outcome)
    assert "observed-control" not in result.answer


@pytest.mark.parametrize("answer", ["safe prose", "The task completed successfully", "O arquivo foi alterado", ""])
def test_partial_evidence_preserves_status_and_rejects_false_claims(answer: str) -> None:
    owner = _owner(_history())
    outcome = _outcome("failed")
    snapshot = _snapshot(owner, outcome)
    result = _finalize(owner, snapshot, answer)
    history = [thaw_projection(item) for item in snapshot.projection_facts.invocation_evidence]
    assert partial_response.has_usable_partial_evidence(outcome, history)
    assert result.answer.startswith("A tarefa n\u00e3o p\u00f4de ser conclu\u00edda.")
    assert "observed-control" in result.answer
    assert "read-control" in result.answer
    assert ("safe prose" in result.answer) == (answer == "safe prose")
    assert "completed successfully" not in result.answer
    assert "O arquivo foi alterado" not in result.answer
    assert result.answer == final_response.compose_operational_answer(outcome, answer, history, None)


@pytest.mark.parametrize(("value", "changes", "usable"), [
    ("preview" * 1000, {"artifacts": [{"metadata": {"complete": False, "truncated": True}}]}, False),
    ("", {}, True), ([], {}, True), (False, {}, True),
    (None, {}, True), (None, {"missing": True}, False),
    ("ignored", {"status": "failed", "ok": False}, False),
], ids=["truncated", "empty-string", "empty-array", "false", "null", "missing", "failed"])
def test_truncated_empty_missing_and_falsy_observations(value: Any, changes: dict[str, Any], usable: bool) -> None:
    owner = _owner(_history(value, **changes))
    owner.agent_state.terminal_disposition = "fail"
    outcome = project_operational_outcome(owner.agent_state, terminal_status="failed")
    history = owner.agent_state.tool_history
    result = _finalize(owner, None)
    assert partial_response.has_usable_partial_evidence(outcome, history) is usable
    assert result.answer.startswith("A tarefa n\u00e3o p\u00f4de ser conclu\u00edda.")
    assert "read-control" in result.answer
    assert len(result.answer) < partial_response.MAX_OBSERVATION_EVIDENCE_CHARS + 500
    assert ("safe prose" in result.answer) is usable
    assert result.answer == final_response.compose_operational_answer(outcome, "safe prose", history, None)


@pytest.mark.parametrize(("outcome", "expected", "calls"), [
    (_outcome(), "safe prose", 0),
    (_outcome(requested_effects=("write",)), "safe prose", 1),
    (_outcome("failed"), "A tarefa terminou com status operacional: failed.", 1),
])
def test_renderer_none_retains_branch_fallback(outcome: OperationalOutcome, expected: str, calls: int,
                                              monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[OperationalOutcome] = []

    def renderer(value: OperationalOutcome) -> None:
        seen.append(value)

    monkeypatch.setattr(run_receipt, "_render_operational_answer", renderer)
    owner = _owner()
    result = _finalize(owner, _snapshot(owner, outcome))
    assert result.answer == expected
    assert seen == [outcome] * calls


@pytest.mark.parametrize("frozen", [True, False])
def test_exact_arguments_renderer_and_report_result_integration(frozen: bool, monkeypatch: pytest.MonkeyPatch) -> None:
    owner = _owner(_history())
    owner.tool_registry = object()
    snapshot = build_canonical_run_snapshot(owner, "succeeded", metrics=project_run_metrics([])) if frozen else None
    history = owner.agent_state.tool_history
    if frozen:
        owner.agent_state.tool_history = _history("later-live-value")
        owner.agent_state.terminal_disposition = "fail"
    composition: list[tuple[Any, ...]] = []
    reports: list[tuple[str, dict[str, Any]]] = []

    def compose(*args: Any) -> str:
        composition.append(args)
        return partial_response.compose_operational_answer(*args)

    def report(answer: str, **kwargs: Any) -> str:
        reports.append((answer, kwargs))
        return "offline-report.md"

    monkeypatch.setattr(run_receipt, "_compose_operational_answer", compose)
    owner._generate_task_report = report
    diagnostics = ({"code": "OFFLINE", "message": "bounded diagnostic"},)
    metadata = {"source": "offline"}
    result = _finalize(owner, snapshot, diagnostics=diagnostics, metadata=metadata, error="bounded error")
    assert len(composition) == 1
    outcome, answer, selected_history, registry, renderer = composition[0]
    assert answer == "safe prose"
    assert registry is owner.tool_registry
    assert renderer is partial_response.render_operational_answer
    assert renderer is final_response.render_operational_answer
    if snapshot is not None:
        assert outcome is snapshot.operational_outcome
        assert selected_history == [thaw_projection(item) for item in snapshot.projection_facts.invocation_evidence]
        assert "later-live-value" not in str(selected_history)
    else:
        assert selected_history is history
        assert outcome == project_operational_outcome(owner.agent_state, terminal_status=result.status)
    assert result.status == "succeeded"
    assert result.answer == "safe prose"
    assert result.diagnostics is diagnostics
    assert result.metadata is metadata
    assert result.error == "bounded error"
    assert result.workspace == "offline-workspace"
    assert result.snapshot is snapshot
    assert result.report_path == result.receipt["report_path"] == "offline-report.md"
    assert len(reports) == 1
    assert reports[0][0] == result.answer
    assert reports[0][1] == dict(status=result.status, error=result.error, receipt=result.receipt, snapshot=snapshot)
    assert reports[0][1]["receipt"] is result.receipt
    assert reports[0][1]["snapshot"] is snapshot


def test_explicit_renderer_output_reaches_result_and_report(monkeypatch: pytest.MonkeyPatch) -> None:
    outcome = _outcome("failed")
    owner = _owner()
    seen: list[Any] = []

    def renderer(value: OperationalOutcome) -> str:
        assert value is outcome
        seen.append(value)
        return "sentinel terminal rendering"

    def report(answer: str, **kwargs: Any) -> str:
        assert answer == "sentinel terminal rendering"
        assert kwargs["status"] == "failed"
        return "offline-report.md"

    monkeypatch.setattr(run_receipt, "_render_operational_answer", renderer)
    owner._generate_task_report = report
    result = _finalize(owner, _snapshot(owner, outcome))
    assert result.answer == "sentinel terminal rendering"
    assert seen == [outcome]


def test_public_wrapper_keeps_its_renderer_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    outcome = _outcome("failed")
    seen: list[Any] = []

    def renderer(value: OperationalOutcome) -> str:
        seen.append(value)
        return "public wrapper renderer"

    monkeypatch.setattr(final_response, "render_operational_answer", renderer)
    assert final_response.compose_operational_answer(outcome, "ignored", [], None) == "public wrapper renderer"
    assert seen == [outcome]
    owner = _owner()
    assert _finalize(owner, _snapshot(owner, outcome)).answer == partial_response.render_operational_answer(outcome)
    assert seen == [outcome]


def test_application_finalization_patch_seam_remains_effective(monkeypatch: pytest.MonkeyPatch) -> None:
    owner = _owner()
    snapshot = build_canonical_run_snapshot(owner, "succeeded", metrics=project_run_metrics([]))
    owner._canonical_run_snapshot = snapshot
    owner._audit_receipt_emitted_run_id = snapshot.correlation.run_id
    sentinel = application_result.AgentRunResult("succeeded", "patched", "offline-workspace")
    calls: list[tuple[Any, ...]] = []

    def finalize(*args: Any, **kwargs: Any) -> Any:
        calls.append((args, kwargs))
        return sentinel

    monkeypatch.setattr(application_result, "finalize_run_result", finalize)
    application = SimpleNamespace(orchestrator=owner, workspace=Path("offline-workspace"))
    assert application_result.finalize_application_result(application, "succeeded", "safe prose") is sentinel
    assert len(calls) == 1
    assert calls[0][1]["snapshot"] is snapshot

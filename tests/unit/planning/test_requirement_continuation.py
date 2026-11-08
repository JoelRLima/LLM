from __future__ import annotations

from types import SimpleNamespace

from llm_agent.agent.planning import requirement_continuation as continuation
from llm_agent.agent.planning.plan_builder import PlanBuildResult, PlanningDecisionKind
from llm_agent.agent.runtime.recovery import RecoveryBudgetState, RecoveryScope


def _review(reason: str, *, status: str = "violated") -> SimpleNamespace:
    return SimpleNamespace(
        accepted=False,
        reason_code=reason,
        requirement_verification={
            "definition": {"task_id": "task", "active_phase_id": "phase"},
            "checks": [{
                "check_id": "check-1", "requirement_id": "req-1",
                "status": status, "reason_code": "value_mismatch",
            }],
        },
    )


def _orchestrator(decision: PlanBuildResult, *, extended: bool = True) -> SimpleNamespace:
    state = SimpleNamespace(
        recovery_budget=RecoveryBudgetState(),
        requirement_replan_fingerprints=(),
        terminal_disposition=None,
        pending_effects=lambda: (),
        pending_obligations=lambda: (),
        blocked_obligations=lambda: (),
        prohibited_effects_occurred=lambda: (),
        unrequested_effects=lambda: (),
        terminal_evidence_complete=lambda: True,
        project_last_result=lambda *_args: None,
    )
    return SimpleNamespace(
        agent_state=state,
        task_policy=None,
        plan_builder=SimpleNamespace(continue_after_reasoning_boundary=lambda _objective: decision),
        execution_gateway=SimpleNamespace(
            extend_validated_plan=lambda *_args, **_kwargs: ["validated"] if extended else None
        ),
        _task_failed=False,
        _cancelled=False,
        _emit=lambda *_args: None,
    )


def test_requirement_repair_extends_only_execute_and_records_one_fingerprint(monkeypatch) -> None:
    monkeypatch.setattr(continuation, "review_task_completion", lambda _owner: _review("REQUIREMENT_VERIFICATION_FAILED"))
    owner = _orchestrator(PlanBuildResult(plan=[{"tool": "file_reader", "args": {}}]))

    result = continuation.continue_failed_requirements(owner, "verify it")

    assert result is not None and result.extended
    assert len(owner.agent_state.requirement_replan_fingerprints) == 1
    assert owner.agent_state.recovery_budget.used(RecoveryScope.LLM_REPLANS) == 1


def test_complete_cannot_waive_check_and_duplicate_failure_is_blocked(monkeypatch) -> None:
    failed = _review("REQUIREMENT_VERIFICATION_FAILED")
    monkeypatch.setattr(continuation, "review_task_completion", lambda _owner: failed)
    owner = _orchestrator(PlanBuildResult(kind=PlanningDecisionKind.COMPLETE))

    result = continuation.continue_failed_requirements(owner, "verify it")
    assert result is not None and result.blocked
    assert owner.agent_state.terminal_disposition == "block"
    assert owner.agent_state.recovery_budget.used(RecoveryScope.LLM_REPLANS) == 1

    owner = _orchestrator(PlanBuildResult(plan=[{"tool": "x", "args": {}}]))
    owner.agent_state.requirement_replan_fingerprints = (continuation._fingerprint(failed.requirement_verification),)
    result = continuation.continue_failed_requirements(owner, "verify it")
    assert result is not None and result.blocked
    assert owner.agent_state.recovery_budget.used(RecoveryScope.LLM_REPLANS) == 0


def test_higher_priority_canonical_blocker_does_not_enter_requirement_repair(monkeypatch) -> None:
    monkeypatch.setattr(continuation, "review_task_completion", lambda _owner: _review("terminal_failure"))
    owner = _orchestrator(PlanBuildResult(plan=[{"tool": "x", "args": {}}]))

    assert continuation.continue_failed_requirements(owner, "verify it") is None
    assert owner.agent_state.recovery_budget.used(RecoveryScope.LLM_REPLANS) == 0


def test_invalid_extension_blocks_after_consuming_single_attempt(monkeypatch) -> None:
    monkeypatch.setattr(continuation, "review_task_completion", lambda _owner: _review("REQUIREMENT_VERIFICATION_INCONCLUSIVE", status="inconclusive"))
    owner = _orchestrator(PlanBuildResult(plan=[{"tool": "x", "args": {}}]), extended=False)

    result = continuation.continue_failed_requirements(owner, "verify it")
    assert result is not None and result.blocked
    assert owner.agent_state.recovery_budget.used(RecoveryScope.LLM_REPLANS) == 1

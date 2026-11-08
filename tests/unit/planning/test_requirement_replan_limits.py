from __future__ import annotations

import pytest

from llm_agent.agent.planning import requirement_continuation as continuation
from llm_agent.agent.planning.plan_builder import PlanBuildResult
from llm_agent.agent.runtime.budget import BudgetExhausted
from llm_agent.agent.runtime.recovery import RecoveryScope
from llm_agent.agent.state import AgentState
from tests.unit.planning.test_requirement_continuation import _orchestrator, _review


def test_exhausted_replan_budget_never_calls_model(monkeypatch):
    review = _review("REQUIREMENT_VERIFICATION_FAILED")
    monkeypatch.setattr(continuation, "review_task_completion", lambda _owner: review)
    owner = _orchestrator(PlanBuildResult())
    assert owner.agent_state.recovery_budget.try_consume(RecoveryScope.LLM_REPLANS)
    calls = []
    owner.plan_builder.continue_after_reasoning_boundary = lambda objective: calls.append(objective)

    result = continuation.continue_failed_requirements(owner, "repair")

    assert result is not None and result.blocked
    assert calls == []
    assert owner.agent_state.requirement_replan_fingerprints == ()


def test_checkpoint_resume_cannot_retry_same_failure_with_new_time_and_bytes(monkeypatch):
    review = _review("REQUIREMENT_VERIFICATION_FAILED")
    monkeypatch.setattr(continuation, "review_task_completion", lambda _owner: review)
    owner = _orchestrator(PlanBuildResult(plan=[{"tool": "file_reader", "args": {}}]))
    owner.agent_state = AgentState()
    first = continuation.continue_failed_requirements(owner, "repair")
    assert first is not None and first.extended
    checkpoint = owner.agent_state.to_checkpoint_dict()
    restored = AgentState()
    restored.from_checkpoint_dict(checkpoint)
    owner.agent_state = restored
    review.requirement_verification["checks"][0].update({
        "observed_at": "2099-01-01T00:00:00Z", "evidence_digest": "different-file-bytes",
    })
    calls = []
    owner.plan_builder.continue_after_reasoning_boundary = lambda objective: calls.append(objective)

    result = continuation.continue_failed_requirements(owner, "repair")

    assert result is not None and result.blocked
    assert calls == []
    assert restored.requirement_replan_fingerprints == tuple(checkpoint["requirement_replan_fingerprints"])
    assert restored.recovery_budget.used(RecoveryScope.LLM_REPLANS) == 1


def test_quantitative_budget_error_propagates_and_attempt_remains_consumed(monkeypatch):
    monkeypatch.setattr(
        continuation, "review_task_completion", lambda _owner: _review("REQUIREMENT_VERIFICATION_FAILED"),
    )
    owner = _orchestrator(PlanBuildResult())

    def exhausted(_objective):
        raise BudgetExhausted("model_calls", 1, 1)

    owner.plan_builder.continue_after_reasoning_boundary = exhausted
    with pytest.raises(BudgetExhausted):
        continuation.continue_failed_requirements(owner, "repair")
    assert len(owner.agent_state.requirement_replan_fingerprints) == 1
    assert owner.agent_state.recovery_budget.used(RecoveryScope.LLM_REPLANS) == 1

"""Helpers for the effect-continuation boundary."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agent.planning.completion_observations import (
    observation_references,
    refresh_executed_effects,
)
from agent.planning.plan_builder import PlanningDecisionKind
from agent.runtime.budget import BudgetExhausted
from agent.runtime.recovery import RecoveryScope


def _consume_effect_budget(
    orchestrator: Any,
    objective: str,
    legacy_increment: Callable[..., None],
    mark_unfinished_effect: Callable[..., str | None],
) -> str | None:
    state = orchestrator.agent_state
    budget = getattr(state, "recovery_budget", None)
    if budget is not None:
        if not budget.try_consume(RecoveryScope.EFFECT_CONTINUATIONS):
            return mark_unfinished_effect(orchestrator, objective)
    else:
        legacy_increment(orchestrator)
    return None

def _request_continuation(orchestrator: Any, objective: str) -> Any:
    refresh_executed_effects(orchestrator)
    state = orchestrator.agent_state
    executed = ", ".join(state.executed_effects) or "nenhum efeito de escrita executado"
    try:
        return orchestrator.plan_builder.continue_after_observation(
            objective, executed, observation_references(orchestrator)
        )
    except BudgetExhausted:
        raise
    except Exception:
        return None


def _apply_continuation(
    orchestrator: Any,
    objective: str,
    continuation: Any,
    *,
    bind_effect_waiver: Callable[..., bool],
    mark_unfinished_effect: Callable[..., str | None],
) -> str | None:
    if continuation.kind is PlanningDecisionKind.COMPLETE:
        index = continuation.waiver_observation_index
        if index is None or not bind_effect_waiver(orchestrator, index):
            return mark_unfinished_effect(orchestrator, objective)
        return None
    if continuation.kind is not PlanningDecisionKind.EXECUTE or not continuation.plan:
        return mark_unfinished_effect(orchestrator, objective)
    orchestrator._emit(
        "continuation_plan_proposed",
        {"steps": len(continuation.plan), "plan": continuation.plan},
    )
    try:
        extension_kwargs: dict[str, Any] = {"allow_conditional_preview": True}
        if getattr(continuation, "planning_view", None) is not None:
            extension_kwargs["planning_view"] = continuation.planning_view
        validated = orchestrator.execution_gateway.extend_validated_plan(
            continuation.plan,
            objective,
            **extension_kwargs,
        )
    except BudgetExhausted:
        raise
    except Exception:
        validated = None
    return None if validated is not None else mark_unfinished_effect(orchestrator, objective)


def continue_after_observation(
    orchestrator: Any,
    objective: str,
    *,
    legacy_increment: Callable[..., None],
    bind_effect_waiver: Callable[..., bool],
    mark_unfinished_effect: Callable[..., str | None],
) -> str | None:
    blocked = _consume_effect_budget(
        orchestrator, objective, legacy_increment, mark_unfinished_effect
    )
    if blocked is not None:
        return blocked
    continuation = _request_continuation(orchestrator, objective)
    if continuation is None:
        return mark_unfinished_effect(orchestrator, objective)
    return _apply_continuation(
        orchestrator,
        objective,
        continuation,
        bind_effect_waiver=bind_effect_waiver,
        mark_unfinished_effect=mark_unfinished_effect,
    )


__all__ = ["continue_after_observation"]

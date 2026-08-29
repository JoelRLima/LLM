"""Explicit legacy response compatibility at the PlanBuilder boundary."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Callable

from agent.llm.admitted_decision_variants import LegacyModelDecision
from agent.planning.plan_model import Plan, PlanDecodeError

if TYPE_CHECKING:
    from agent.planning.plan_builder import PlanBuildResult


def build_legacy_initial(
    builder: Any, decision: LegacyModelDecision
) -> "PlanBuildResult":
    """Adapt the retained initial-plan response without making it canonical."""

    from agent.planning.plan_builder import PlanBuildResult, PlanningDecisionKind

    plan = legacy_plan(builder, decision)
    if plan is None:
        return PlanBuildResult(kind=PlanningDecisionKind.FAIL)
    obligations = decision.payload.get("obligations")
    obligations_ok, reviewed_obligations = builder._review_obligations(
        obligations if isinstance(obligations, Sequence) else None,
        source="initial_plan_compatibility",
    )
    if not obligations_ok:
        return PlanBuildResult(kind=PlanningDecisionKind.FAIL)
    if not plan:
        return PlanBuildResult(kind=PlanningDecisionKind.REPLAN)
    return PlanBuildResult(
        plan=plan,
        obligations=reviewed_obligations,
        continue_after_plan=decision.payload.get("action") == "continue_after_plan",
        planning_view=builder._last_planning_view,
    )


def build_legacy_continuation(
    builder: Any, decision: LegacyModelDecision
) -> "PlanBuildResult":
    """Adapt the retained continuation response at its explicit edge."""

    from agent.planning.plan_builder import PlanBuildResult, PlanningDecisionKind

    plan = legacy_plan(builder, decision)
    if not plan:
        return PlanBuildResult(
            kind=PlanningDecisionKind.FAIL,
            planning_view=builder._last_planning_view,
        )
    return PlanBuildResult(
        plan=plan,
        kind=PlanningDecisionKind.EXECUTE,
        planning_view=builder._last_planning_view,
    )


def legacy_plan(builder: Any, decision: LegacyModelDecision) -> Plan | None:
    raw_plan = decision.payload.get("plan")
    if not isinstance(raw_plan, Sequence):
        return None
    try:
        return Plan.from_raw(raw_plan, new_step_id=continuation_step_id(builder))
    except (PlanDecodeError, TypeError, ValueError):
        return None


def continuation_step_id(builder: Any) -> Callable[[], str] | None:
    state = getattr(builder.orchestrator, "agent_state", None)
    if not getattr(state, "plan", None):
        return None
    factory = getattr(state, "_new_step_id", None)
    return factory if callable(factory) else None


__all__ = [
    "build_legacy_continuation",
    "build_legacy_initial",
    "continuation_step_id",
    "legacy_plan",
]

"""Bounded deterministic and model-assisted plan recovery."""

from __future__ import annotations

from typing import Any, Dict, Optional, cast

from agent.planning import replan_validation as _replan_validation
from agent.planning.plan_model import ToolPlanStep
from agent.planning.planning_context import PlanningContextSnapshot
from agent.planning.presentation import PlanningPresentationSnapshot
from agent.planning.replan_compat import (
    LegacyReplanContext,
    RetryPolicy,
    legacy_replan_context,
    legacy_replan_failure,
)
from agent.planning.replan_llm import ask_llm_for_alternative
from agent.planning.replan_models import (
    ErrorCategory,
    ReplanAction,
    category_for_failure,
)
from agent.planning.replan_models import ReplanContext as CoreReplanContext
from agent.runtime.failures import FailureFact
from agent.runtime.logging import logger
from agent.runtime.recovery import RecoveryScope

__all__ = [
    "ErrorCategory",
    "ReplanAction",
    "ReplanContext",
    "RetryPolicy",
    "ask_llm_for_alternative",
    "replan",
    "try_heuristic",
]


def _legacy_context(*args: Any, **kwargs: Any) -> CoreReplanContext | LegacyReplanContext:
    """Keep old construction syntax outside the canonical context type."""
    if any(
        key in kwargs
        for key in ("retry_counts", "heuristic_replans", "llm_replans")
    ):
        return legacy_replan_context(*args, **kwargs)
    return CoreReplanContext(*args, **kwargs)


# Compatibility symbols are construction facades, not policy owners.
ReplanContext = _legacy_context
ReplanContextCompat = _legacy_context

_planning_view = _replan_validation._planning_view
_recovery_budget = _replan_validation._recovery_budget
_surviving_plan = _replan_validation._surviving_plan
_surviving_plan_with_admission = _replan_validation._surviving_plan_with_admission
_surviving_steps = _replan_validation._surviving_steps
_validate_and_optimize_new_steps = _replan_validation._validate_and_optimize_new_steps
try_heuristic = _replan_validation.try_heuristic


def _log_action(
    context: CoreReplanContext, category: ErrorCategory, action: ReplanAction
) -> None:
    logger.info(
        "[REPLAN] step=%s tool=%s error=%s strategy=%s replacement=%s",
        len(context.tool_history) + 1,
        context.current_step.get("tool"),
        category.value,
        action.source,
        [
            step.tool if isinstance(step, ToolPlanStep) else "deferred_condition"
            for step in action.steps
        ],
    )


def _canonical_context(
    ctx: CoreReplanContext | LegacyReplanContext,
    failure: FailureFact | str | None,
) -> CoreReplanContext:
    if isinstance(ctx, LegacyReplanContext):
        typed_failure = (
            failure
            if isinstance(failure, FailureFact)
            else legacy_replan_failure(failure)
        )
        return CoreReplanContext(
            task=ctx.task,
            current_step=ctx.current_step,
            tool_history=ctx.tool_history,
            failure=typed_failure,
            last_exception=ctx.last_exception,
            budget_remaining=ctx.budget_remaining,
        )
    if isinstance(failure, FailureFact):
        if failure is ctx.failure:
            return ctx
        return CoreReplanContext(
            task=ctx.task,
            current_step=ctx.current_step,
            tool_history=ctx.tool_history,
            failure=failure,
            last_tool_result=ctx.last_tool_result,
            last_exception=ctx.last_exception,
            budget_remaining=ctx.budget_remaining,
        )
    return CoreReplanContext(
        task=ctx.task,
        current_step=ctx.current_step,
        tool_history=ctx.tool_history,
        failure=legacy_replan_failure(failure),
        last_tool_result=ctx.last_tool_result,
        last_exception=ctx.last_exception,
        budget_remaining=ctx.budget_remaining,
    )


def replan(
    ctx: CoreReplanContext | LegacyReplanContext,
    failure: FailureFact | str | None,
    orchestrator: Any,
    retry_policy: RetryPolicy | None = None,
    *,
    planning_context: PlanningContextSnapshot | None = None,
    planning_view: PlanningPresentationSnapshot | None = None,
    validation_repair: bool = False,
    repairable_fields: tuple[str, ...] = (),
    prior_steps: tuple[Any, ...] = (),
) -> Optional[ReplanAction]:
    del retry_policy
    ctx = _canonical_context(ctx, failure)
    category = category_for_failure(ctx.failure)
    if not validation_repair and ctx.failure.retryable and not ctx.failure.hard:
        action = try_heuristic(
            category,
            str(ctx.current_step.get("tool", "")),
            ctx.current_step.get("args", {}),
        )
        budget = _recovery_budget(orchestrator)
        if action is not None and (
            budget is None or budget.try_consume(RecoveryScope.HEURISTIC_REPLANS)
        ):
            action = _validate_and_optimize_new_steps(
                action,
                orchestrator,
                planning_context,
                planning_view,
                objective=ctx.task,
            )
        else:
            action = None
        if action is not None:
            _log_action(ctx, category, action)
            return action
    if validation_repair or (ctx.failure.retryable and not ctx.failure.hard):
        budget = _recovery_budget(orchestrator)
        allowed = validation_repair or budget is None or budget.try_consume(
            RecoveryScope.LLM_REPLANS
        )
        action = (
            ask_llm_for_alternative(
                cast(Dict[str, Any], ctx.current_step),
                ctx.failure,
                orchestrator,
                validation_repair=validation_repair,
                repairable_fields=repairable_fields,
                prior_steps=prior_steps,
                objective=ctx.task,
            )
            if allowed
            else None
        )
        if validation_repair:
            if action is not None:
                _log_action(ctx, category, action)
            return action
        action = _validate_and_optimize_new_steps(
            action,
            orchestrator,
            planning_context,
            planning_view,
            objective=ctx.task,
        )
        if action is not None:
            _log_action(ctx, category, action)
            return action
    logger.warning(
        "[REPLAN] step=%s tool=%s error=%s strategy=abort",
        len(ctx.tool_history) + 1,
        ctx.current_step.get("tool"),
        category.value,
    )
    return None

"""Bounded deterministic and model-assisted plan recovery."""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

from agent.planning.plan_optimizer import PlanOptimizer
from agent.planning.plan_validator import PlanValidator
from agent.planning.planning_context import (
    PlanningContextError,
    PlanningContextSnapshot,
)
from agent.planning.presentation import PlanningPresentationSnapshot, validate_planning_view_binding
from agent.planning.replan_llm import ask_llm_for_alternative
from agent.planning.replan_models import (
    ErrorCategory,
    ReplanAction,
    ReplanContext,
    RetryPolicy,
    classify_error,
)
from agent.planning.replan_scope import scoped_replan_observations
from agent.planning.tool_metadata import TOOL_METADATA
from agent.runtime.logging import logger

__all__ = [
    "ErrorCategory",
    "ReplanAction",
    "ReplanContext",
    "RetryPolicy",
    "ask_llm_for_alternative",
    "classify_error",
    "replan",
    "try_heuristic",
]


def try_heuristic(
    category: ErrorCategory, tool: str, args: Dict[str, Any]
) -> Optional[ReplanAction]:
    del tool
    if category != ErrorCategory.FILE_NOT_FOUND:
        return None
    file_path = args.get("file_path") or args.get("target") or ""
    if not file_path:
        return None
    return ReplanAction(
        steps=[
            {"tool": "directory_lister", "args": {"path": os.path.dirname(file_path) or "."}},
        ],
        source="heuristic",
        reason=f"FileNotFound: '{file_path}' — tentando localizar o arquivo.",
    )


def _validate_and_optimize_new_steps(
    action: Optional[ReplanAction],
    orchestrator: Any,
    planning_context: PlanningContextSnapshot | None = None,
    planning_view: PlanningPresentationSnapshot | None = None,
    *,
    objective: str = "",
) -> Optional[ReplanAction]:
    if not action or not action.steps:
        return action
    explicit_context = planning_context is not None
    context = planning_context or getattr(orchestrator, "planning_context", None)
    presentation = getattr(action, "planning_view", None) or planning_view
    if context is None and presentation is not None:
        raise PlanningContextError("planning view sem contexto canÃ´nico")
    if context is not None and presentation is not None:
        validate_planning_view_binding(context, presentation, "linear")
    elif explicit_context:
        raise PlanningContextError("contexto explÃ­cito exige view correlacionada")
    elif context is not None:
        presentation = _planning_view(orchestrator, context)
    scoped_plan_id, scoped_observations = scoped_replan_observations(orchestrator, action.steps)
    validator = PlanValidator(
        getattr(orchestrator, "skills", {}) or {},
        getattr(orchestrator, "active_skills", []) or [],
        getattr(orchestrator, "allowed_capabilities", None),
        getattr(orchestrator, "tool_registry", None),
        planning_context=context,
        presented_names=presentation.presented_names if presentation is not None else None,
        planning_view=presentation,
        objective=objective,
        available_observations=scoped_observations,
        plan_identity=scoped_plan_id,
    )
    surviving = _surviving_steps(action.steps, validator, "replan")
    if not surviving:
        return None
    if context is None:
        optimized = PlanOptimizer(TOOL_METADATA).optimize(surviving).optimized_steps
    else:
        optimized = PlanOptimizer(
            planning_context=context,
            presented_names=presentation.presented_names if presentation is not None else None,
            planning_view=presentation,
        ).optimize(surviving).optimized_steps
    final_steps = _surviving_steps(optimized, validator, "replan pÃ³s-otimizaÃ§Ã£o")
    if not final_steps:
        return None
    action.steps = final_steps
    return action


def _planning_view(
    orchestrator: Any,
    context: PlanningContextSnapshot | None,
) -> PlanningPresentationSnapshot | None:
    if context is None:
        return None
    return context.resolve_view("linear", getattr(orchestrator, "active_skills", ()))


def _surviving_steps(
    steps: list[Dict[str, Any]], validator: PlanValidator, phase: str
) -> list[Dict[str, Any]]:
    report = validator.validate(steps)
    for warning in report.warnings:
        logger.info("[VALIDATOR][%s] %s", phase, warning)
    for error in report.errors:
        logger.warning("[VALIDATOR][%s] %s", phase, error)
    blocked = {item.index for item in report.blocked_steps}
    # ``is_valid`` is the canonical structural gate.  A validator may report
    # structural errors before it can associate them with a concrete step, so
    # an empty blocked-step list is not evidence that the replacement is safe.
    if report.errors or not report.is_valid or blocked:
        logger.warning(
            "[VALIDATOR][%s] replacement rejeitado integralmente; valido=%s erros=%s passos_bloqueados=%s",
            phase,
            report.is_valid,
            len(report.errors),
            sorted(index + 1 for index in blocked),
        )
        return []
    return list(steps)


def _log_action(context: ReplanContext, category: ErrorCategory, action: ReplanAction) -> None:
    logger.info(
        "[REPLAN] step=%s tool=%s error=%s strategy=%s replacement=%s",
        len(context.tool_history) + 1,
        context.current_step.get("tool"),
        category.value,
        action.source,
        [step["tool"] for step in action.steps],
    )


def replan(
    ctx: ReplanContext,
    error_message: str,
    orchestrator: Any,
    retry_policy: RetryPolicy | None = None,
    *,
    planning_context: PlanningContextSnapshot | None = None,
    planning_view: PlanningPresentationSnapshot | None = None,
    validation_repair: bool = False,
    repairable_fields: tuple[str, ...] = (),
    prior_steps: tuple[Any, ...] = (),
) -> Optional[ReplanAction]:
    policy = retry_policy or RetryPolicy()
    category = classify_error(error_message)
    if not validation_repair and policy.allows_heuristic(ctx):
        action = try_heuristic(category, ctx.current_step.get("tool", ""), ctx.current_step.get("args", {}))
        action = _validate_and_optimize_new_steps(
            action, orchestrator, planning_context, planning_view, objective=ctx.task
        )
        if action is not None:
            ctx.record("heuristic")
            _log_action(ctx, category, action)
            return action
    if policy.allows_llm(ctx):
        action = ask_llm_for_alternative(
            ctx.current_step,
            error_message,
            orchestrator,
            validation_repair=validation_repair,
            repairable_fields=repairable_fields,
            prior_steps=prior_steps,
            objective=ctx.task,
        )
        if validation_repair:
            if action is not None:
                ctx.record("llm")
                _log_action(ctx, category, action)
            return action
        action = _validate_and_optimize_new_steps(
            action, orchestrator, planning_context, planning_view, objective=ctx.task
        )
        if action is not None:
            ctx.record("llm")
            _log_action(ctx, category, action)
            return action
    logger.warning(
        "[REPLAN] step=%s tool=%s error=%s strategy=abort",
        len(ctx.tool_history) + 1,
        ctx.current_step.get("tool"),
        category.value,
    )
    return None

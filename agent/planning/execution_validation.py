"""Validation/optimization orchestration for the execution gateway."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, cast

from agent.planning.plan_validator import PlanValidator
from agent.planning.planning_context import PlanningContextSnapshot
from agent.planning.presentation import PlanningPresentationSnapshot


def validate_and_optimize_plan(
    gateway: Any,
    plan: List[Dict[str, Any]],
    objective: str,
    *,
    planning_context: PlanningContextSnapshot | None = None,
    planning_view: PlanningPresentationSnapshot | None = None,
) -> Optional[List[Dict[str, Any]]]:
    explicit_context = planning_context is not None
    context = (
        planning_context
        if planning_context is not None
        else getattr(gateway.orchestrator, "planning_context", None)
    )
    gateway._active_planning_context = context
    presentation = gateway._planning_view(
        context, "linear", planning_view, explicit_context
    )
    validator = _validator(gateway, context, presentation, objective)
    report = validator.validate(plan)
    gateway._log_validation(report)
    if not report.is_valid:
        gateway._abort(
            "plano inválido",
            [*report.errors, *(item.reason for item in report.blocked_steps)],
        )
        return None
    blocked_steps = report.blocked_steps
    working_plan = gateway._bind_deferred_references(plan)
    if working_plan is not plan:
        validator = _validator(
            gateway,
            context,
            presentation,
            objective,
            canonical_deferred_references=True,
        )
        bound_report = validator.validate(working_plan)
        gateway._log_validation(bound_report, "binding canônico")
        if not bound_report.is_valid:
            gateway._abort(
                "binding deferred inválido",
                [*bound_report.errors, *(item.reason for item in bound_report.blocked_steps)],
            )
            return None
        blocked_steps = bound_report.blocked_steps
    recovered = gateway._recover(
        working_plan,
        objective,
        blocked_steps,
        "replanejamento inicial falhou",
        context,
        presentation,
    )
    if recovered is None:
        return None
    optimized = gateway._optimize(
        recovered,
        context,
        presentation.presented_names if presentation else None,
        presentation,
    )
    post_report = validator.validate(optimized)
    gateway._log_validation(post_report, "pós-otimização")
    if not post_report.is_valid:
        gateway._abort(
            "plano inválido pós-otimização",
            [*post_report.errors, *(item.reason for item in post_report.blocked_steps)],
        )
        return None
    return cast(Optional[List[Dict[str, Any]]], gateway._recover(
        optimized,
        objective,
        post_report.blocked_steps,
        "replanejamento pós-otimização falhou",
        context,
        presentation,
    ))


def _validator(
    gateway: Any,
    context: PlanningContextSnapshot | None,
    presentation: PlanningPresentationSnapshot | None,
    objective: str,
    *,
    canonical_deferred_references: bool = False,
) -> PlanValidator:
    return PlanValidator(
        gateway.orchestrator.skills,
        gateway.orchestrator.active_skills,
        getattr(gateway.orchestrator, "allowed_capabilities", None),
        getattr(gateway.orchestrator, "tool_registry", None),
        planning_context=context,
        presented_names=presentation.presented_names if presentation is not None else None,
        planning_view=presentation,
        objective=objective,
        canonical_deferred_references=canonical_deferred_references,
    )

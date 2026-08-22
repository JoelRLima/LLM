"""Validation/optimization orchestration for the execution gateway."""

from __future__ import annotations

from collections.abc import Mapping
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
    observations, plan_identity = _observation_scope(gateway, plan)
    validator = _validator(
        gateway,
        context,
        presentation,
        objective,
        plan_identity=plan_identity,
        available_observations=observations,
    )
    repair_budget = {"remaining": 1}
    report = validator.validate(plan)
    gateway._log_validation(report)
    if not report.is_valid and not _repairable_report(report):
        gateway._abort(
            "plano inválido",
            [*report.errors, *(item.reason for item in report.blocked_steps)],
        )
        return None
    blocked_steps = report.blocked_steps
    working_plan = gateway._bind_deferred_references(plan)
    if working_plan is not plan:
        bound_observations, bound_plan_identity = _observation_scope(
            gateway, working_plan
        )
        validator = _validator(
            gateway,
            context,
            presentation,
            objective,
            canonical_deferred_references=True,
            plan_identity=bound_plan_identity,
            available_observations=bound_observations,
        )
        bound_report = validator.validate(working_plan)
        gateway._log_validation(bound_report, "binding canônico")
        if not bound_report.is_valid and not _repairable_report(bound_report):
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
        repair_budget,
    )
    if recovered is None:
        return None
    optimized = gateway._optimize(
        recovered,
        context,
        presentation.presented_names if presentation else None,
        presentation,
    )
    post_validator = validator
    if _has_canonical_references(optimized):
        post_observations, post_plan_identity = _observation_scope(gateway, optimized)
        post_validator = _validator(
            gateway,
            context,
            presentation,
            objective,
            canonical_deferred_references=True,
            plan_identity=post_plan_identity,
            available_observations=post_observations,
        )
    post_report = post_validator.validate(optimized)
    gateway._log_validation(post_report, "pós-otimização")
    if not post_report.is_valid and not _repairable_report(post_report):
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
        repair_budget,
    ))


def _has_canonical_references(plan: List[Dict[str, Any]]) -> bool:
    for step in plan:
        if not isinstance(step, Mapping):
            continue
        if step.get("kind") == "deferred_condition" and isinstance(
            step.get("observation_ref"), str
        ):
            return True
        bindings = step.get("bindings")
        if not isinstance(bindings, Mapping):
            continue
        if any(
            isinstance(spec, Mapping) and isinstance(spec.get("from_step"), str)
            for spec in bindings.values()
        ):
            return True
    return False


def _repairable_report(report: Any) -> bool:
    """Allow the bounded repair path to handle field-scoped blocked steps."""

    return bool(
        report.blocked_steps
        and not report.errors
        and all(item.is_validation_repair for item in report.blocked_steps)
    )


def _validator(
    gateway: Any,
    context: PlanningContextSnapshot | None,
    presentation: PlanningPresentationSnapshot | None,
    objective: str,
    *,
    canonical_deferred_references: bool = False,
    plan_identity: str | None = None,
    available_observations: Any = None,
) -> PlanValidator:
    observations = (
        getattr(gateway.orchestrator.agent_state, "tool_history", ())
        if available_observations is None
        else available_observations
    )
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
        available_observations=observations,
        plan_identity=plan_identity,
    )


def _observation_scope(gateway: Any, plan: List[Dict[str, Any]]) -> tuple[tuple[Any, ...], str | None]:
    """Scope planner observations to the plan being validated.

    A fresh plan must not inherit marker-looking literals from a previous
    plan.  An extension or revalidation that carries a persisted step ID may
    use only the matching plan identity.
    """

    state = getattr(gateway.orchestrator, "agent_state", None)
    plan_identity = getattr(state, "plan_identity", None)
    current_ids = {
        str(step.get("_step_id"))
        for step in getattr(state, "plan", ())
        if isinstance(step, Mapping) and step.get("_step_id")
    }
    candidate_ids = {
        str(step.get("_step_id"))
        for step in plan
        if isinstance(step, Mapping) and step.get("_step_id")
    }
    if plan_identity is None or not current_ids.intersection(candidate_ids):
        return (), None
    return tuple(getattr(state, "tool_history", ()) or ()), str(plan_identity)

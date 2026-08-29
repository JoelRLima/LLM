"""Validation/optimization orchestration for the execution gateway."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Optional

from agent.planning.plan_admission import PlanAdmissionMode, PlanAdmissionService
from agent.planning.plan_model import Plan
from agent.planning.planning_context import PlanningContextSnapshot
from agent.planning.presentation import PlanningPresentationSnapshot


def validate_and_optimize_plan(
    gateway: Any,
    plan: Plan | Sequence[Mapping[str, Any]],
    objective: str,
    *,
    planning_context: PlanningContextSnapshot | None = None,
    planning_view: PlanningPresentationSnapshot | None = None,
    allow_conditional_preview: bool = False,
) -> Optional[Plan]:
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
    admission = PlanAdmissionService(gateway.orchestrator)
    report = admission.admit(
        plan,
        objective,
        mode=PlanAdmissionMode.INITIAL,
        planning_context=context,
        planning_view=presentation,
        allow_conditional_preview=allow_conditional_preview,
    )
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
        bound_report = admission.admit(
            working_plan,
            objective,
            mode=PlanAdmissionMode.BOUND,
            planning_context=context,
            planning_view=presentation,
            allow_conditional_preview=allow_conditional_preview,
        )
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
    )
    if recovered is None:
        return None
    optimized = gateway._optimize(
        recovered,
        context,
        presentation.presented_names if presentation else None,
        presentation,
    )
    post_report = admission.admit(
        optimized,
        objective,
        mode=PlanAdmissionMode.POST_OPTIMIZATION,
        planning_context=context,
        planning_view=presentation,
        allow_conditional_preview=allow_conditional_preview,
    )
    gateway._log_validation(post_report, "pós-otimização")
    if not post_report.is_valid and not _repairable_report(post_report):
        gateway._abort(
            "plano inválido pós-otimização",
            [*post_report.errors, *(item.reason for item in post_report.blocked_steps)],
        )
        return None
    recovered = gateway._recover(
        optimized,
        objective,
        post_report.blocked_steps,
        "replanejamento pós-otimização falhou",
        context,
        presentation,
    )
    return recovered if isinstance(recovered, Plan) else None


def _repairable_report(report: Any) -> bool:
    """Allow the bounded repair path to handle field-scoped blocked steps."""

    return bool(
        report.blocked_steps
        and not report.errors
        and all(item.is_validation_repair for item in report.blocked_steps)
    )

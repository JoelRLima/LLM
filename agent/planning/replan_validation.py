"""Validation, optimization and compatibility helpers for replan actions."""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

from agent.planning.plan_admission import PlanAdmissionMode, PlanAdmissionService
from agent.planning.plan_model import Plan
from agent.planning.plan_optimizer import PlanOptimizer
from agent.planning.planning_context import PlanningContextError, PlanningContextSnapshot
from agent.planning.presentation import (
    PlanningPresentationSnapshot,
    validate_planning_view_binding,
)
from agent.planning.replan_models import ErrorCategory, ReplanAction
from agent.planning.tool_metadata import TOOL_METADATA
from agent.runtime.logging import logger


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
        steps=Plan.from_raw(
            [{"tool": "directory_lister", "args": {"path": os.path.dirname(file_path) or "."}}]
        ),
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
        raise PlanningContextError("planning view sem contexto canônico")
    if context is not None and presentation is not None:
        validate_planning_view_binding(context, presentation, "linear")
    elif explicit_context:
        raise PlanningContextError("contexto explícito exige view correlacionada")
    elif context is not None:
        presentation = _planning_view(orchestrator, context)
    admission = PlanAdmissionService(orchestrator)
    surviving = _surviving_plan_with_admission(
        action.steps,
        admission,
        objective,
        planning_context=context,
        planning_view=presentation,
        phase="replan",
    )
    if surviving is None:
        return None
    optimized = _optimize_plan(surviving, context, presentation)
    final_plan = _surviving_plan_with_admission(
        optimized,
        admission,
        objective,
        planning_context=context,
        planning_view=presentation,
        phase="replan post-optimization",
    )
    if final_plan is None:
        return None
    action.steps = final_plan
    return action


def _optimize_plan(
    plan: Plan,
    context: PlanningContextSnapshot | None,
    presentation: PlanningPresentationSnapshot | None,
) -> Plan:
    if context is None:
        return PlanOptimizer(TOOL_METADATA).optimize(plan).optimized_plan
    return PlanOptimizer(
        planning_context=context,
        presented_names=presentation.presented_names if presentation is not None else None,
        planning_view=presentation,
    ).optimize(plan).optimized_plan


def _planning_view(
    orchestrator: Any,
    context: PlanningContextSnapshot | None,
) -> PlanningPresentationSnapshot | None:
    if context is None:
        return None
    return context.resolve_view("linear", getattr(orchestrator, "active_skills", ()))


def _recovery_budget(orchestrator: Any) -> Any:
    return getattr(getattr(orchestrator, "agent_state", None), "recovery_budget", None)


def _surviving_steps(
    steps: Plan | list[Dict[str, Any]], validator: Any, phase: str
) -> list[Dict[str, Any]]:
    if isinstance(steps, Plan):
        surviving = _surviving_plan(steps, validator, phase)
        return surviving.to_legacy() if surviving is not None else []
    report = validator.validate(steps)
    _log_validation_report(report, phase)
    blocked = {item.index for item in report.blocked_steps}
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


def _surviving_plan(plan: Plan, validator: Any, phase: str) -> Plan | None:
    report = validator.validate(plan)
    _log_validation_report(report, phase)
    blocked = {item.index for item in report.blocked_steps}
    if report.errors or not report.is_valid or blocked:
        logger.warning(
            "[VALIDATOR][%s] replacement rejected; valid=%s errors=%s blocked=%s",
            phase,
            report.is_valid,
            len(report.errors),
            sorted(index + 1 for index in blocked),
        )
        return None
    return plan


def _surviving_plan_with_admission(
    plan: Plan,
    admission: PlanAdmissionService,
    objective: str,
    *,
    planning_context: PlanningContextSnapshot | None,
    planning_view: PlanningPresentationSnapshot | None,
    phase: str,
) -> Plan | None:
    report = admission.admit(
        plan,
        objective,
        mode=PlanAdmissionMode.REPLAN,
        planning_context=planning_context,
        planning_view=planning_view,
    )
    _log_validation_report(report, phase)
    blocked = {item.index for item in report.blocked_steps}
    if report.errors or not report.is_valid or blocked:
        logger.warning(
            "[VALIDATOR][%s] replacement rejected; valid=%s errors=%s blocked=%s",
            phase,
            report.is_valid,
            len(report.errors),
            sorted(index + 1 for index in blocked),
        )
        return None
    return plan


def _log_validation_report(report: Any, phase: str) -> None:
    for warning in report.warnings:
        logger.info("[VALIDATOR][%s] %s", phase, warning)
    for error in report.errors:
        logger.warning("[VALIDATOR][%s] %s", phase, error)

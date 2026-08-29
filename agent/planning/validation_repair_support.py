"""Shared validation-repair admission helpers for typed and legacy edges."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Dict, List, Optional

from agent.planning.plan_model import (
    DeferredConditionStep,
    Plan,
    PlanDecodeError,
    PlanReferenceError,
    ToolPlanStep,
)


def _validation_context(gateway: Any, planning_context: Any, planning_view: Any) -> tuple[Any, Any]:
    from agent.planning.presentation import validate_planning_view_binding

    context = planning_context or getattr(gateway.orchestrator, "planning_context", None)
    presentation = planning_view
    if context is not None and presentation is not None:
        validate_planning_view_binding(context, presentation, "linear")
    elif context is not None and callable(getattr(gateway, "_planning_view", None)):
        presentation = gateway._planning_view(context, "linear")
    return context, presentation


def _validate_reintegrated_candidate(
    gateway: Any,
    candidate: List[Dict[str, Any]],
    objective: str,
    repaired_index: int,
    planning_context: Any,
    planning_view: Any,
    allowed_blocked_indices: set[int],
) -> Optional[List[Dict[str, Any]]]:
    from agent.planning.plan_admission import PlanAdmissionMode, PlanAdmissionService
    from agent.runtime.logging import logger

    context, presentation = _validation_context(gateway, planning_context, planning_view)
    prepared = _prepare_candidate(
        gateway, candidate, _has_deferred_or_result_bindings(candidate), logger
    )
    if prepared is None:
        return None
    report = PlanAdmissionService(gateway.orchestrator).admit(
        prepared,
        objective,
        mode=PlanAdmissionMode.VALIDATION_REPAIR,
        planning_context=context,
        planning_view=presentation,
    )
    for error in report.errors:
        logger.warning("[VALIDATOR][validation repair] %s", error)
    blocked_indexes = {item.index for item in report.blocked_steps}
    if report.errors or repaired_index in blocked_indexes:
        return None
    if blocked_indexes - (allowed_blocked_indices - {repaired_index}):
        return None
    return prepared.to_legacy()


def _prepare_candidate(
    gateway: Any,
    candidate: List[Dict[str, Any]],
    has_references: bool,
    logger: Any,
) -> Plan | None:
    if has_references:
        binder = getattr(gateway, "_bind_deferred_references", None)
        if not callable(binder):
            return None
        try:
            prepared = binder(candidate)
            return prepared if isinstance(prepared, Plan) else None
        except (TypeError, ValueError, KeyError) as exc:
            logger.warning("Candidate de reparo não pôde ser canonicalizado: %s", exc)
            return None
    try:
        return Plan.from_raw(candidate)
    except (PlanDecodeError, TypeError, ValueError) as exc:
        logger.warning("Candidate de reparo não possui plano tipado: %s", exc)
        return None


def _validate_typed_candidate(
    gateway: Any,
    candidate: Plan,
    objective: str,
    repaired_index: int,
    planning_context: Any,
    planning_view: Any,
    allowed_blocked_indices: set[int],
) -> Plan | None:
    from agent.planning.plan_admission import PlanAdmissionMode, PlanAdmissionService
    from agent.runtime.logging import logger

    context, presentation = _validation_context(gateway, planning_context, planning_view)
    has_references = _typed_has_plan_references(candidate)
    try:
        prepared = (
            gateway._bind_deferred_references(candidate)
            if has_references
            else candidate
        )
    except (PlanReferenceError, TypeError, ValueError, KeyError) as exc:
        logger.warning("Candidate de reparo não pôde ser canonicalizado: %s", exc)
        return None
    report = PlanAdmissionService(gateway.orchestrator).admit(
        prepared,
        objective,
        mode=PlanAdmissionMode.VALIDATION_REPAIR,
        planning_context=context,
        planning_view=presentation,
    )
    for error in report.errors:
        logger.warning("[VALIDATOR][validation repair] %s", error)
    blocked_indexes = {item.index for item in report.blocked_steps}
    if report.errors or repaired_index in blocked_indexes:
        return None
    if blocked_indexes - (allowed_blocked_indices - {repaired_index}):
        return None
    return prepared


def _typed_has_plan_references(plan: Plan) -> bool:
    return any(
        isinstance(step, DeferredConditionStep)
        or (isinstance(step, ToolPlanStep) and step.bindings is not None)
        for step in plan.steps
    )


def _has_deferred_or_result_bindings(plan: Plan | List[Dict[str, Any]]) -> bool:
    if isinstance(plan, Plan):
        return _typed_has_plan_references(plan)
    return any(
        isinstance(step, Mapping)
        and (step.get("kind") == "deferred_condition" or "bindings" in step)
        for step in plan
    )


__all__ = [
    "_has_deferred_or_result_bindings",
    "_validate_reintegrated_candidate",
    "_validate_typed_candidate",
]

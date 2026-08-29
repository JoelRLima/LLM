"""Narrow runtime contract for one mechanically deferred equals decision."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from agent.planning.plan_model import (
    DeferredConditionStep,
    Plan,
    PlanDecodeError,
    PlanReferenceError,
    ToolPlanStep,
    bind_plan_references,
    resolve_deferred_observation_reference,
    serialize_plan,
)

DEFERRED_CONDITION_KIND = "deferred_condition"


def is_deferred_condition(step: Any) -> bool:
    if isinstance(step, DeferredConditionStep):
        return True
    # Explicit model/checkpoint compatibility probe. Typed consumers never
    # use this branch after the plan has crossed the decode boundary.
    return isinstance(step, Mapping) and step.get("kind") == DEFERRED_CONDITION_KIND


def bind_deferred_observation_references(
    plan: Plan | Sequence[Mapping[str, Any]],
) -> Plan | list[dict[str, Any]]:
    """Bind deferred references through the single typed resolver owner."""

    if isinstance(plan, Plan):
        return bind_plan_references(plan)
    try:
        typed_plan = Plan.from_raw(plan)
        return serialize_plan(bind_plan_references(typed_plan))
    except (PlanDecodeError, PlanReferenceError, TypeError, ValueError) as exc:
        raise ValueError(str(exc)) from exc


def validate_deferred_condition(
    step: Any,
    index: int,
    plan: Plan | Sequence[Mapping[str, Any]],
    objective: str,
) -> str | None:
    """Validate the closed MVP shape without interpreting user intent."""

    if not is_deferred_condition(step):
        return "item deferred sem kind canônico"
    try:
        typed_plan = plan if isinstance(plan, Plan) else Plan.from_raw(plan)
        typed_step = typed_plan[index]
    except (IndexError, PlanDecodeError, PlanReferenceError, TypeError, ValueError) as exc:
        return str(exc)
    if not isinstance(typed_step, DeferredConditionStep):
        return "item deferred sem kind canônico"
    if typed_step.on_true.bindings is not None:
        return "on_true deve conter somente tool e args"
    try:
        referenced = typed_plan[
            resolve_deferred_observation_reference(typed_step, index, typed_plan)
        ]
    except (PlanReferenceError, IndexError, TypeError):
        return "observation_ref deve apontar para um passo anterior existente"
    if not isinstance(referenced, ToolPlanStep):
        return "observation_ref deve apontar para um ToolStep anterior"
    if not typed_step.predicate.value:
        return "predicate.value deve ser literal textual não vazio"
    if typed_step.predicate.value not in objective:
        return "predicate.value não está literalmente presente no objetivo original"
    return None


def evaluate_equals(observation: Any, literal: str) -> bool:
    if not isinstance(observation, str):
        raise ValueError("observação textual canônica indisponível")
    return observation == literal


__all__ = [
    "DEFERRED_CONDITION_KIND",
    "bind_deferred_observation_references",
    "evaluate_equals",
    "is_deferred_condition",
    "validate_deferred_condition",
]

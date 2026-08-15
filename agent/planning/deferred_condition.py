"""Narrow runtime contract for one mechanically deferred equals decision."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

DEFERRED_CONDITION_KIND = "deferred_condition"


def is_deferred_condition(step: Any) -> bool:
    return isinstance(step, dict) and step.get("kind") == DEFERRED_CONDITION_KIND


def _resolve_observation_index(
    reference: Any,
    index: int,
    plan: Sequence[Mapping[str, Any]],
) -> int | None:
    if type(reference) is int:
        resolved = reference - 1
        return resolved if 0 <= resolved < index else None
    if isinstance(reference, str) and reference:
        matches = [
            candidate
            for candidate in range(index)
            if plan[candidate].get("_step_id") == reference
        ]
        return matches[0] if len(matches) == 1 else None
    return None


def bind_deferred_observation_references(
    plan: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Bind model-local ordinals to the existing canonical step identity."""

    bound = [dict(step) for step in plan]
    for index, step in enumerate(bound):
        if not is_deferred_condition(step):
            continue
        reference = step.get("observation_ref")
        if type(reference) is not int:
            continue
        observation_index = _resolve_observation_index(reference, index, bound)
        if observation_index is None:
            continue
        step_id = bound[observation_index].get("_step_id")
        if isinstance(step_id, str) and step_id:
            step["observation_ref"] = step_id
    return bound


def validate_deferred_condition(
    step: Any,
    index: int,
    plan: Sequence[Mapping[str, Any]],
    objective: str,
) -> str | None:
    """Validate the closed MVP shape without interpreting user intent."""

    if not is_deferred_condition(step):
        return "item deferred sem kind canônico"
    shape_problem = _validate_deferred_shape(step)
    if shape_problem:
        return shape_problem
    reference_problem = _validate_observation_reference(step, index, plan)
    if reference_problem:
        return reference_problem
    predicate_problem = _validate_predicate(step, objective)
    if predicate_problem:
        return predicate_problem
    return _validate_branches(step)


def _validate_deferred_shape(step: Mapping[str, Any]) -> str | None:
    allowed = {
        "_step_id",
        "kind",
        "observation_ref",
        "predicate",
        "on_true",
        "on_false",
    }
    unknown = set(step) - allowed
    if unknown:
        return f"campos não suportados: {', '.join(sorted(unknown))}"
    return None


def _validate_observation_reference(
    step: Mapping[str, Any],
    index: int,
    plan: Sequence[Mapping[str, Any]],
) -> str | None:
    reference = step.get("observation_ref")
    observation_index = _resolve_observation_index(reference, index, plan)
    if observation_index is None:
        return "observation_ref deve apontar para um passo anterior existente"
    referenced = plan[observation_index]
    if is_deferred_condition(referenced) or not isinstance(referenced.get("tool"), str):
        return "observation_ref deve apontar para um ToolStep anterior"
    return None


def _validate_predicate(step: Mapping[str, Any], objective: str) -> str | None:
    predicate = step.get("predicate")
    if not isinstance(predicate, dict) or set(predicate) != {"op", "value"}:
        return "predicate deve conter somente op e value"
    if predicate.get("op") != "equals":
        return "operador de predicate não suportado; somente equals é aceito"
    value = predicate.get("value")
    if not isinstance(value, str) or not value:
        return "predicate.value deve ser literal textual não vazio"
    if value not in objective:
        return "predicate.value não está literalmente presente no objetivo original"
    return None


def _validate_branches(step: Mapping[str, Any]) -> str | None:
    on_true = step.get("on_true")
    if not isinstance(on_true, dict) or is_deferred_condition(on_true):
        return "on_true deve conter um ToolStep concreto"
    if set(on_true) != {"tool", "args"}:
        return "on_true deve conter somente tool e args"
    if not isinstance(on_true.get("tool"), str) or not isinstance(
        on_true.get("args"), dict
    ):
        return "on_true deve conter tool e args"

    on_false = step.get("on_false")
    if on_false != {"waive_effect": "write"}:
        return "on_false deve reutilizar exatamente a waiver canônica de write"
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

"""Invocation-time resolution for already validated result bindings."""

from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from typing import Any, cast

from agent.planning.plan_model import (
    Plan,
    PlanDecodeError,
    PlanReferenceError,
    ToolPlanStep,
    resolve_result_binding_reference,
)
from agent.planning.result_binding_types import ResultBindingError
from agent.planning.result_binding_values import json_detach, result_is_bindable
from agent.planning.result_bindings_schema import _safe_target
from agent.state_progression import current_result_for_step


def _read_path(data: Any, path: Sequence[str | int]) -> Any:
    value = data
    for segment in path:
        if isinstance(segment, str) and isinstance(value, Mapping) and segment in value:
            value = value[segment]
        elif (
            isinstance(segment, int)
            and isinstance(value, (list, tuple))
            and 0 <= segment < len(value)
        ):
            value = value[segment]
        else:
            raise ResultBindingError("binding.path não está presente no resultado")
    return json_detach(value)


def _resolve_bound_args_typed(
    step: ToolPlanStep,
    index: int,
    plan: Plan,
    history: Sequence[Mapping[str, Any]],
    plan_id: str | None,
) -> dict[str, Any]:
    args = _detached_mapping(step.args)
    if step.bindings is None:
        return args
    for target, binding in step.bindings.items():
        try:
            source_index = resolve_result_binding_reference(binding, index, plan)
        except PlanReferenceError as exc:
            raise ResultBindingError(str(exc)) from exc
        source_id = plan[source_index].step_id
        current = current_result_for_step(history, source_id, plan_id=plan_id)
        if current is None:
            raise ResultBindingError("resultado canônico do passo referenciado indisponível")
        _, entry = current
        result = entry.get("result")
        if not isinstance(result, Mapping) or not result_is_bindable(result):
            raise ResultBindingError("resultado referenciado ausente, falho ou incompleto")
        args[_safe_target(target)] = _read_path(result.get("data"), binding.path)
    return args


def resolve_bound_args(
    step: ToolPlanStep | Mapping[str, Any],
    index: int,
    plan: Plan | Sequence[Mapping[str, Any]],
    history: Sequence[Mapping[str, Any]],
    *,
    plan_id: str | None = None,
) -> dict[str, Any]:
    """Resolve one typed step; raw input is an explicit compatibility edge."""
    if isinstance(plan, Plan):
        typed_plan = plan
    else:
        try:
            typed_plan = Plan.from_raw(plan)
        except (PlanDecodeError, TypeError, ValueError) as exc:
            raise ResultBindingError("plano não possui forma canônica") from exc
    typed_step = step if isinstance(step, ToolPlanStep) else typed_plan[index]
    if not isinstance(typed_step, ToolPlanStep):
        raise ResultBindingError("resolve_bound_args exige um ToolPlanStep")
    return _resolve_bound_args_typed(typed_step, index, typed_plan, history, plan_id)


def _detached_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    """Copy immutable typed arguments into an invocation-local mutable map."""

    def detach(item: Any) -> Any:
        if isinstance(item, Mapping):
            return {key: detach(child) for key, child in item.items()}
        if isinstance(item, tuple):
            return [detach(child) for child in item]
        if isinstance(item, list):
            return [detach(child) for child in item]
        if isinstance(item, frozenset):
            return [detach(child) for child in item]
        return copy.deepcopy(item)

    return cast(dict[str, Any], detach(value))

"""Strict, data-only references from earlier canonical tool results."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any, cast

from agent.planning.plan_model import (
    Plan,
    PlanDecodeError,
    PlanReferenceError,
    PlanStepReference,
    ResultBinding,
    ToolPlanStep,
    bind_plan_references,
    resolve_previous_step_reference,
    resolve_result_binding_reference,
    serialize_plan,
)
from agent.planning.result_binding_types import ResultBindingError
from agent.planning.result_binding_values import result_is_bindable
from agent.planning.result_bindings_resolution import resolve_bound_args
from agent.planning.result_bindings_schema import (
    _safe_target,
    _schema_at_path,
    validate_path,
    validate_path_against_schema,
)


def has_result_bindings(step: Any) -> bool:
    if isinstance(step, ToolPlanStep):
        return step.bindings is not None
    # Explicit legacy/model-shape probe at the compatibility boundary.
    return isinstance(step, Mapping) and "bindings" in step


def _binding_items(raw: Any) -> list[tuple[str, Mapping[str, Any]]]:
    if not isinstance(raw, Mapping):
        raise ResultBindingError("bindings deve ser um objeto target -> especificacao")
    if any(
        not isinstance(key, str) or not isinstance(value, Mapping)
        for key, value in raw.items()
    ):
        raise ResultBindingError("bindings deve mapear target para objeto")
    return list(raw.items())


def binding_items(
    step: ToolPlanStep | Mapping[str, Any],
) -> list[tuple[str, ResultBinding | Mapping[str, Any]]]:
    if isinstance(step, ToolPlanStep):
        return list((step.bindings or {}).items())
    # Serializer/model compatibility adapter; typed consumers use attrs.
    return cast(
        list[tuple[str, ResultBinding | Mapping[str, Any]]],
        _binding_items(step["bindings"]) if "bindings" in step else [],
    )


def _source_and_path(spec: ResultBinding | Mapping[str, Any]) -> tuple[Any, Any]:
    if isinstance(spec, ResultBinding):
        return spec.from_step.to_raw(), spec.path
    return spec.get("from_step"), spec.get("path", ())


def _resolve_ordinal(
    source: Any, index: int, plan: Sequence[Mapping[str, Any]]
) -> int | None:
    """Compatibility adapter to the single typed reference resolver."""

    try:
        typed_plan = Plan.from_raw(plan, preserve_step_ids=type(source) is not int)
        return resolve_previous_step_reference(
            PlanStepReference.from_raw(source), index, typed_plan
        )
    except (PlanReferenceError, ValueError, TypeError):
        return None


def _validate_typed_spec(
    target: str,
    binding: ResultBinding,
    index: int,
    plan: Plan,
    canonical: bool,
    args: Mapping[str, Any],
    seen: set[str],
    result_data_schema_resolver: Callable[[Any], Mapping[str, Any] | None]
    | None = None,
    target_schema_resolver: Callable[[Any, str], Mapping[str, Any] | None]
    | None = None,
) -> None:
    target = _safe_target(target)
    if target in seen:
        raise ResultBindingError("target duplicado")
    if target in args:
        raise ResultBindingError(f"target '{target}' colide com args concretos")
    seen.add(target)
    if canonical and not binding.from_step.is_stable_id:
        raise ResultBindingError("from_step deve ser ordinal local ou ID estável")
    if not canonical and not binding.from_step.is_ordinal:
        raise ResultBindingError("from_step deve ser ordinal local ou ID estável")
    try:
        source_index = resolve_result_binding_reference(binding, index, plan)
    except PlanReferenceError as exc:
        raise ResultBindingError(str(exc)) from exc
    producer = plan[source_index]
    if not isinstance(producer, ToolPlanStep):
        raise ResultBindingError("from_step deve apontar para ToolStep anterior")
    result_schema = (
        result_data_schema_resolver(producer)
        if result_data_schema_resolver is not None
        else None
    )
    normalized_path = validate_path_against_schema(binding.path, result_schema)
    if target_schema_resolver is not None and result_schema is not None:
        source_leaf = _schema_at_path(normalized_path, result_schema)
        target_schema = target_schema_resolver(plan[index], target)
        source_type = source_leaf.get("type") if source_leaf is not None else None
        target_type = target_schema.get("type") if target_schema is not None else None
        simple_types = {
            "array",
            "boolean",
            "integer",
            "null",
            "number",
            "object",
            "string",
        }
        if (
            source_type in simple_types
            and target_type in simple_types
            and source_type != target_type
        ):
            raise ResultBindingError(
                f"tipo do resultado ({source_type}) incompatível com argumento "
                f"'{target}' ({target_type})"
            )


def validate_result_bindings(
    plan: Plan | Sequence[Mapping[str, Any]],
    *,
    canonical_references: bool = False,
    result_data_schema_resolver: Callable[[Any], Mapping[str, Any] | None]
    | None = None,
    target_schema_resolver: Callable[[Any, str], Mapping[str, Any] | None]
    | None = None,
) -> list[str]:
    """Validate typed bindings; list-shaped input is decoded once here."""

    if not isinstance(plan, Plan):
        try:
            plan = Plan.from_raw(plan)
        except (PlanDecodeError, TypeError, ValueError) as exc:
            return [f"Plano binding inválido: {exc}."]
    errors: list[str] = []
    for index, step in enumerate(plan.steps):
        if not isinstance(step, ToolPlanStep) or step.bindings is None:
            continue
        try:
            seen: set[str] = set()
            for target, binding in step.bindings.items():
                _validate_typed_spec(
                    target,
                    binding,
                    index,
                    plan,
                    canonical_references,
                    step.args,
                    seen,
                    result_data_schema_resolver,
                    target_schema_resolver,
                )
        except (PlanReferenceError, ResultBindingError) as exc:
            errors.append(f"Passo {index + 1} binding inválido: {exc}.")
    return errors


def bind_result_references(
    plan: Plan | Sequence[Mapping[str, Any]], new_step_id: Any
) -> Plan | list[dict[str, Any]]:
    """Bind references in the typed owner and serialize only for legacy callers."""

    if isinstance(plan, Plan):
        return bind_plan_references(plan)
    allocated: set[str] = set()

    def allocate() -> str:
        base = str(new_step_id())
        candidate = base
        suffix = 2
        while candidate in allocated:
            candidate = f"{base}-{suffix}"
            suffix += 1
        allocated.add(candidate)
        return candidate

    try:
        typed_plan = Plan.from_raw(plan, new_step_id=allocate)
        return serialize_plan(bind_plan_references(typed_plan))
    except (PlanDecodeError, PlanReferenceError, TypeError, ValueError) as exc:
        raise ResultBindingError(str(exc)) from exc


def referenced_step_ids(plan: Plan | Sequence[Mapping[str, Any]]) -> set[str]:
    if not isinstance(plan, Plan):
        try:
            plan = bind_plan_references(Plan.from_raw(plan))
        except (PlanDecodeError, PlanReferenceError, TypeError, ValueError):
            return set()
    refs: set[str] = set()
    for step in plan.steps:
        if not isinstance(step, ToolPlanStep) or step.bindings is None:
            continue
        for binding in step.bindings.values():
            if binding.from_step.step_id is not None:
                refs.add(binding.from_step.step_id)
    return refs


def binding_targets(step: ToolPlanStep | Mapping[str, Any]) -> set[str]:
    return {_safe_target(target) for target, _ in binding_items(step)}


__all__ = [
    "ResultBindingError",
    "bind_result_references",
    "binding_items",
    "binding_targets",
    "has_result_bindings",
    "referenced_step_ids",
    "resolve_bound_args",
    "result_is_bindable",
    "validate_path",
    "validate_path_against_schema",
    "validate_result_bindings",
]

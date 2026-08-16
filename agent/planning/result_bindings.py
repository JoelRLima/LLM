"""Strict, data-only references from earlier canonical tool results."""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping, Sequence
from typing import Any

from agent.state_progression import current_result_for_step
from agent.tools.result_completeness import canonical_completeness


class ResultBindingError(ValueError):
    pass


_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_MAX_PATH, _MAX_PATH_KEY = 32, 128


def has_result_bindings(step: Any) -> bool:
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


def binding_items(step: Mapping[str, Any]) -> list[tuple[str, Mapping[str, Any]]]:
    return _binding_items(step["bindings"]) if "bindings" in step else []


def _source_and_path(spec: Mapping[str, Any]) -> tuple[Any, Any]:
    return spec.get("from_step"), spec.get("path", ())


def _safe_target(target: Any) -> str:
    if not isinstance(target, str) or not target or not _SEGMENT.fullmatch(target):
        raise ResultBindingError("binding.target deve ser um nome de argumento simples")
    return target


def validate_path(path: Any) -> tuple[str | int, ...]:
    if not isinstance(path, list) or len(path) > _MAX_PATH:
        raise ResultBindingError("binding.path deve ser uma lista limitada de segmentos")
    normalized: list[str | int] = []
    for segment in path:
        if type(segment) is int and 0 <= segment <= 1_000_000:
            normalized.append(segment)
        elif isinstance(segment, str) and 0 < len(segment) <= _MAX_PATH_KEY and not segment.startswith("__") and all(ord(char) >= 32 for char in segment):
            normalized.append(segment)
        else:
            raise ResultBindingError("segmento de path inválido")
    return tuple(normalized)


def _resolve_ordinal(source: Any, index: int, plan: Sequence[Mapping[str, Any]]) -> int | None:
    if type(source) is int:
        candidate = source - 1
        return candidate if 0 <= candidate < index else None
    if isinstance(source, str) and source:
        matches = [i for i in range(index) if plan[i].get("_step_id") == source]
        return matches[0] if len(matches) == 1 else None
    return None


def _validate_spec(target: str, spec: Mapping[str, Any], index: int, plan: Sequence[Mapping[str, Any]], canonical: bool, args: Mapping[str, Any], seen: set[str]) -> None:
    target = _safe_target(target)
    if target in seen:
        raise ResultBindingError("target duplicado")
    if target in args:
        raise ResultBindingError(f"target '{target}' colide com args concretos")
    seen.add(target)
    if set(spec) - {"from_step", "path"} or not {"from_step", "path"}.issubset(spec):
        raise ResultBindingError("campos não suportados no binding")
    source, path = _source_and_path(spec)
    if isinstance(source, Mapping) or (canonical and not isinstance(source, str)) or (not canonical and type(source) is not int):
        raise ResultBindingError("from_step deve ser ordinal local ou ID estável")
    source_index = _resolve_ordinal(source, index, plan)
    if source_index is None or not isinstance(plan[source_index].get("tool"), str) or plan[source_index].get("kind") == "deferred_condition":
        raise ResultBindingError("from_step deve apontar para ToolStep anterior")
    validate_path(path)


def validate_result_bindings(plan: Sequence[Mapping[str, Any]], *, canonical_references: bool = False) -> list[str]:
    errors: list[str] = []
    for index, step in enumerate(plan):
        if not isinstance(step, Mapping) or "bindings" not in step:
            continue
        try:
            raw_args = step.get("args")
            args = raw_args if isinstance(raw_args, Mapping) else {}
            seen: set[str] = set()
            for target, spec in binding_items(step):
                _validate_spec(target, spec, index, plan, canonical_references, args, seen)
        except ResultBindingError as exc:
            errors.append(f"Passo {index + 1} binding inválido: {exc}.")
    return errors


def _normalize_binding(target: str, spec: Mapping[str, Any], index: int, plan: Sequence[Mapping[str, Any]], new_step_id: Any) -> dict[str, Any]:
    source, path = _source_and_path(spec)
    source_index = _resolve_ordinal(source, index, plan)
    if source_index is None:
        raise ResultBindingError("from_step deve apontar para passo anterior")
    normalized = dict(spec)
    normalized["from_step"] = plan[source_index].get("_step_id") or new_step_id()
    normalized["path"] = list(validate_path(path))
    return normalized


def bind_result_references(plan: Sequence[Mapping[str, Any]], new_step_id: Any) -> list[dict[str, Any]]:
    bound = [dict(step) for step in plan]
    seen_ids: set[str] = set()
    for step in bound:
        candidate = step.get("_step_id")
        if not isinstance(candidate, str) or not candidate or candidate in seen_ids:
            candidate = str(new_step_id())
            step["_step_id"] = candidate
        seen_ids.add(candidate)
        if isinstance(step.get("args"), Mapping):
            step["args"] = dict(step["args"])
    for index, step in enumerate(bound):
        if "bindings" not in step:
            continue
        items = binding_items(step)
        normalized = [_normalize_binding(target, spec, index, bound, new_step_id) for target, spec in items]
        step["bindings"] = {
            target: value
            for (target, _), value in zip(items, normalized, strict=True)
        }
    return bound


def referenced_step_ids(plan: Sequence[Mapping[str, Any]]) -> set[str]:
    refs: set[str] = set()
    for step in plan:
        for _, spec in binding_items(step) if isinstance(step, Mapping) else ():
            source, _ = _source_and_path(spec)
            if source is not None:
                refs.add(str(source))
    return refs


def binding_targets(step: Mapping[str, Any]) -> set[str]:
    return {_safe_target(target) for target, _ in binding_items(step)}


def _json_detach(value: Any) -> Any:
    if value is None or type(value) in (str, int, float, bool):
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_detach(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_detach(item) for item in value]
    raise ResultBindingError("resultado contém valor não JSON vinculável")


def _complete_result(result: Mapping[str, Any]) -> bool:
    if result.get("ok") is not True or result.get("executed") is not True or result.get("status") != "succeeded":
        return False
    if "data" not in result or not canonical_completeness(result)[0]:
        return False
    return True


def _read_path(data: Any, path: Sequence[str | int]) -> Any:
    value = data
    for segment in path:
        if isinstance(segment, str) and isinstance(value, Mapping) and segment in value:
            value = value[segment]
        elif isinstance(segment, int) and isinstance(value, (list, tuple)) and segment < len(value):
            value = value[segment]
        else:
            raise ResultBindingError("binding.path não está presente no resultado")
    return _json_detach(value)


def resolve_bound_args(step: Mapping[str, Any], index: int, plan: Sequence[Mapping[str, Any]], history: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    raw_args = step.get("args")
    args = copy.deepcopy(dict(raw_args)) if isinstance(raw_args, Mapping) else {}
    for target, spec in binding_items(step):
        source, path = _source_and_path(spec)
        source_index = _resolve_ordinal(source, index, plan)
        source_id = plan[source_index].get("_step_id") if source_index is not None else source
        current = current_result_for_step(history, str(source_id))
        if current is None:
            raise ResultBindingError("resultado canônico do passo referenciado indisponível")
        _, entry = current
        if current is None:
            raise ResultBindingError("resultado canônico do passo referenciado indisponível")
        result = entry.get("result")
        if not isinstance(result, Mapping) or not _complete_result(result):
            raise ResultBindingError("resultado referenciado ausente, falho ou incompleto")
        args[_safe_target(target)] = _read_path(result.get("data"), validate_path(path))
    return args


__all__ = [
    "ResultBindingError", "bind_result_references", "binding_items", "binding_targets", "has_result_bindings",
    "referenced_step_ids", "resolve_bound_args", "validate_path", "validate_result_bindings",
]

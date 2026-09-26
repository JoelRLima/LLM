"""Pure bounded schema and immutable JSON metadata primitives.

This module is intentionally independent of skills, tools, and planning.  It
owns only the structural/value mechanics shared by those consumer domains.
"""

from __future__ import annotations

import math
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any

MAX_SCHEMA_DEPTH = 64


class PlanningSchemaError(ValueError):
    """Raised when a planning schema exceeds a structural safety budget."""


def validate_schema_depth(value: Any, *, max_depth: int = MAX_SCHEMA_DEPTH) -> None:
    """Validate depth, cycles and object keys without Python recursion."""

    stack: list[tuple[Any, int, tuple[int, ...]]] = [(value, 0, ())]
    while stack:
        node, depth, ancestors = stack.pop()
        if depth > max_depth:
            raise PlanningSchemaError("schema de planning excede a profundidade maxima")
        children = _schema_children(node)
        if children is None:
            continue
        node_id = id(node)
        if node_id in ancestors:
            raise PlanningSchemaError("schema de planning contem ciclo")
        next_ancestors = (*ancestors, node_id)
        stack.extend((child, depth + 1, next_ancestors) for child in children)


def validate_planning_schema_shape(value: Any) -> None:
    """Validate the JSON-like fields consumed directly by planning."""

    if not isinstance(value, Mapping):
        raise PlanningSchemaError("schema de planning requer raiz mapping")
    if "properties" in value:
        _validate_properties_shape(value["properties"])
    if "required" in value:
        _validate_required_shape(value["required"])


def _validate_properties_shape(properties: Any) -> None:
    if not isinstance(properties, Mapping):
        raise PlanningSchemaError("campo de schema 'properties' requer mapping")
    for name, property_schema in properties.items():
        if not isinstance(name, str):
            raise PlanningSchemaError("nomes de properties devem ser textuais")
        _validate_property_shape(property_schema)


def _validate_property_shape(property_schema: Any) -> None:
    if not isinstance(property_schema, Mapping):
        raise PlanningSchemaError("schemas de properties requerem mapping")
    if "type" in property_schema and not isinstance(property_schema["type"], str):
        raise PlanningSchemaError("campo de schema 'type' requer texto")


def _validate_required_shape(required: Any) -> None:
    if not isinstance(required, list):
        raise PlanningSchemaError("campo de schema 'required' requer lista")
    if any(not isinstance(item, str) for item in required):
        raise PlanningSchemaError("itens de schema 'required' requerem texto")


def _schema_children(node: Any) -> tuple[Any, ...] | None:
    if isinstance(node, Mapping):
        children: list[Any] = []
        for key, child in node.items():
            if not isinstance(key, str):
                raise PlanningSchemaError("schema de planning requer chaves textuais")
            children.append(child)
        return tuple(children)
    if isinstance(node, (list, tuple)):
        return tuple(node)
    return None


@dataclass(frozen=True, slots=True)
class FrozenJsonObject(Mapping[str, Any]):
    """Immutable JSON-like mapping."""

    _items: tuple[tuple[str, Any], ...]

    def __getitem__(self, key: str) -> Any:
        for item_key, value in self._items:
            if item_key == key:
                return value
        raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        return (key for key, _ in self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Mapping):
            return dict(self.items()) == dict(other.items())
        return NotImplemented


def thaw_json_like(value: Any) -> Any:
    """Rebuild a fresh mutable public JSON value from an internal snapshot."""

    if isinstance(value, FrozenJsonObject):
        return {key: thaw_json_like(item) for key, item in value._items}
    if isinstance(value, tuple):
        return [thaw_json_like(item) for item in value]
    return value


def _freeze_scalar(value: Any) -> tuple[bool, Any]:
    if value is None:
        return True, value
    if type(value) in {str, bool, int}:
        return True, value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("float is not finite JSON")
        return True, value
    return False, None


def _freeze_mapping(value: Mapping[str, Any], active: set[int]) -> FrozenJsonObject:
    identity = id(value)
    if identity in active:
        raise ValueError("cyclic JSON structure is not supported")
    active.add(identity)
    try:
        frozen_items = []
        for key, item in value.items():
            if type(key) is not str:
                raise TypeError("JSON object keys must be strings")
            frozen_items.append((key, freeze_json_like(item, _active=active)))
        return FrozenJsonObject(tuple(frozen_items))
    finally:
        active.remove(identity)


def _freeze_sequence(value: list[Any] | tuple[Any, ...], active: set[int]) -> tuple[Any, ...]:
    identity = id(value)
    if identity in active:
        raise ValueError("cyclic JSON structure is not supported")
    active.add(identity)
    try:
        return tuple(freeze_json_like(item, _active=active) for item in value)
    finally:
        active.remove(identity)


def freeze_json_like(value: Any, *, _active: set[int] | None = None) -> Any:
    """Copy and recursively freeze strict JSON-like values."""

    is_scalar, frozen_scalar = _freeze_scalar(value)
    if is_scalar:
        return frozen_scalar
    active = _active if _active is not None else set()
    if isinstance(value, Mapping):
        return _freeze_mapping(value, active)
    if isinstance(value, (list, tuple)):
        return _freeze_sequence(value, active)
    raise TypeError(f"valor nao e JSON-like: {type(value).__name__}")


_RESULT_DATA_SCHEMA_TYPES = frozenset(
    {"array", "boolean", "integer", "null", "number", "object", "string"}
)
_RESULT_DATA_SCHEMA_KEYS = frozenset({"type", "properties", "items"})


def validate_result_data_schema(value: Any) -> None:
    """Validate bounded, data-only result structure metadata."""

    if not isinstance(value, Mapping):
        raise TypeError("result_data_schema deve ser um mapping JSON-like")
    try:
        validate_schema_depth(value, max_depth=16)
    except PlanningSchemaError as exc:
        raise ValueError("result_data_schema excede o limite estrutural") from exc
    pending: list[Mapping[str, Any]] = [value]
    for _ in range(256):
        if not pending:
            return
        pending.extend(_result_schema_children(pending.pop()))
    raise ValueError("result_data_schema excede o limite de elementos")


def freeze_result_data_schema(value: Mapping[str, Any] | None) -> Any:
    if value is None:
        return None
    validate_result_data_schema(value)
    return freeze_json_like(dict(value))


def _result_schema_children(node: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    if any(type(key) is not str for key in node):
        raise TypeError("result_data_schema requer chaves textuais")
    if set(node) - _RESULT_DATA_SCHEMA_KEYS:
        raise ValueError("result_data_schema contém campos não suportados")
    schema_type = node.get("type")
    if schema_type is not None and (
        type(schema_type) is not str or schema_type not in _RESULT_DATA_SCHEMA_TYPES
    ):
        raise ValueError("result_data_schema.type contém valor não suportado")
    children: list[Mapping[str, Any]] = []
    if "properties" in node:
        children.extend(_result_schema_properties(node["properties"], schema_type))
    if "items" in node:
        children.append(_result_schema_items(node["items"], schema_type))
    return children


def _result_schema_properties(value: Any, schema_type: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, Mapping):
        raise TypeError("result_data_schema.properties deve ser um mapping")
    if schema_type is not None and schema_type != "object":
        raise ValueError("result_data_schema.properties requer type object")
    children: list[Mapping[str, Any]] = []
    for name, child in value.items():
        if type(name) is not str or not isinstance(child, Mapping):
            raise TypeError("result_data_schema.properties contém schema inválido")
        children.append(child)
    return children


def _result_schema_items(value: Any, schema_type: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError("result_data_schema.items deve ser um objeto de schema")
    if schema_type is not None and schema_type != "array":
        raise ValueError("result_data_schema.items requer type array")
    return value


def result_data_schema_for_contract(contract: Any) -> Mapping[str, Any] | None:
    if contract is None:
        return None
    schema = getattr(contract, "result_data_schema", None)
    if isinstance(schema, Mapping):
        return schema
    spec = getattr(contract, "spec", None)
    schema = getattr(spec, "result_data_schema", None)
    return schema if isinstance(schema, Mapping) else None


def target_schema_for_contract(contract: Any, target: str) -> Mapping[str, Any] | None:
    if contract is None:
        return None
    schema = getattr(contract, "input_schema", None) or getattr(contract, "schema", None)
    if not isinstance(schema, Mapping):
        return None
    properties = schema.get("properties")
    if not isinstance(properties, Mapping):
        properties = {
            key: value
            for key, value in schema.items()
            if key not in {"type", "required", "properties", "additionalProperties"}
        }
    target_schema = properties.get(target)
    return target_schema if isinstance(target_schema, Mapping) else None


__all__ = [
    "FrozenJsonObject",
    "MAX_SCHEMA_DEPTH",
    "PlanningSchemaError",
    "freeze_json_like",
    "freeze_result_data_schema",
    "result_data_schema_for_contract",
    "target_schema_for_contract",
    "thaw_json_like",
    "validate_planning_schema_shape",
    "validate_result_data_schema",
    "validate_schema_depth",
]

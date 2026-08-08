"""Bounded, non-recursive validation for planner JSON schemas."""

from __future__ import annotations

from collections.abc import Mapping
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


__all__ = [
    "MAX_SCHEMA_DEPTH",
    "PlanningSchemaError",
    "validate_planning_schema_shape",
    "validate_schema_depth",
]

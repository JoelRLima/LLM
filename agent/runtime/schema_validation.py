"""Shared pure argument-schema validation for planning and invocation.

The runtime owns the JSON-schema vocabulary used at both boundaries. Legacy
builtin skills may still expose a direct ``{field: description}`` mapping; it
is normalized here so planning and concrete invocation cannot drift.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent.runtime.schema_validation_values import validate_value

MAX_SCHEMA_DEPTH = 64
MAX_SCHEMA_NODES = 4096
SUPPORTED_TYPES = frozenset(
    {"string", "integer", "number", "boolean", "object", "array", "null"}
)
_SCHEMA_KEYS = frozenset(
    {
        "$schema", "additionalItems", "additionalProperties", "description",
        "enum", "items", "maximum", "maxItems", "maxLength", "minimum",
        "minItems", "minLength", "properties", "required", "title", "type",
    }
)


def _schema_children(node: Any) -> tuple[Any, ...] | None:
    if isinstance(node, Mapping):
        for key in node:
            if type(key) is not str:
                raise ValueError("schema keys must be strings")
        return tuple(node.values())
    if isinstance(node, (list, tuple)):
        return tuple(node)
    return None


def validate_schema_structure(schema: Any) -> None:
    """Reject cyclic, excessively deep, or oversized schema structures."""

    if not isinstance(schema, Mapping):
        raise ValueError("schema must be an object")
    pending: list[tuple[Any, int, tuple[int, ...]]] = [(schema, 0, ())]
    visited = 0
    while pending:
        node, depth, ancestors = pending.pop()
        visited += 1
        if visited > MAX_SCHEMA_NODES:
            raise ValueError("schema exceeds the structural element limit")
        if depth > MAX_SCHEMA_DEPTH:
            raise ValueError("schema exceeds the structural depth limit")
        children = _schema_children(node)
        if children is None:
            continue
        identity = id(node)
        if identity in ancestors:
            raise ValueError("cyclic schema is not supported")
        next_ancestors = (*ancestors, identity)
        pending.extend((child, depth + 1, next_ancestors) for child in children)


def _legacy_property(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        prefix = value.split(":", 1)[0].strip().lower()
        expected = prefix if prefix in SUPPORTED_TYPES else "string"
        return {"type": expected, "description": value}
    raise ValueError("legacy argument schemas must contain objects or descriptions")


def normalize_argument_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
    """Return the canonical object-schema shape used by both validators."""

    if not isinstance(schema, Mapping):
        raise ValueError("schema must be an object")
    normalized = dict(schema)
    properties = normalized.get("properties")
    if properties is None:
        legacy_fields = {
            key: value
            for key, value in normalized.items()
            if key not in _SCHEMA_KEYS
        }
        if legacy_fields:
            properties = legacy_fields
    if properties is not None:
        if not isinstance(properties, Mapping):
            raise ValueError("schema properties must be an object")
        canonical_properties: dict[str, dict[str, Any]] = {}
        for key, value in properties.items():
            if type(key) is not str:
                raise ValueError("schema property names must be strings")
            canonical_properties[key] = _legacy_property(value)
        normalized["properties"] = canonical_properties
    normalized.setdefault("type", "object")
    validate_schema_structure(normalized)
    return normalized


def validate_schema_arguments(
    schema: Mapping[str, Any],
    args: Mapping[str, Any],
    *,
    bound_fields: set[str] | None = None,
    planning: bool = False,
) -> None:
    """Validate one object with identical rules in planning/runtime modes."""

    effective_schema = normalize_argument_schema(schema)
    _validate_argument_object(
        effective_schema,
        args,
        set(bound_fields or ()),
        planning=planning,
    )


def _validate_argument_object(
    schema: Mapping[str, Any],
    args: Mapping[str, Any],
    bound: set[str],
    *,
    planning: bool,
) -> None:
    if not isinstance(args, Mapping):
        raise ValueError("arguments must be a JSON object")
    properties = schema.get("properties") or {}
    if not isinstance(properties, Mapping):
        raise ValueError("schema properties must be an object")
    unknown_bound = sorted(str(key) for key in bound if key not in properties)
    if unknown_bound:
        raise ValueError(f"argument(s) vinculados desconhecidos: {', '.join(unknown_bound)}")
    _validate_unknown_arguments(schema, properties, args)
    _validate_required_arguments(schema, properties, args, bound)
    for key, value in args.items():
        property_schema = properties.get(key)
        if property_schema is not None:
            validate_value(str(key), value, property_schema, planning=planning)


def _validate_unknown_arguments(
    schema: Mapping[str, Any], properties: Mapping[str, Any], args: Mapping[str, Any],
) -> None:
    if schema.get("additionalProperties") is False:
        unknown = sorted(str(key) for key in args if key not in properties)
        if unknown:
            raise ValueError(f"unknown argument(s): {', '.join(unknown)}")


def _validate_required_arguments(
    schema: Mapping[str, Any], properties: Mapping[str, Any], args: Mapping[str, Any], bound: set[str],
) -> None:
    del properties
    required = schema.get("required") or ()
    required_values = required if isinstance(required, (list, tuple)) else (required,)
    for key in required_values:
        if not isinstance(key, str):
            raise ValueError("schema required entries must be strings")
        if key not in bound and key not in args:
            raise ValueError(f"missing required argument: {key}")


__all__ = [
    "MAX_SCHEMA_DEPTH",
    "MAX_SCHEMA_NODES",
    "normalize_argument_schema",
    "validate_schema_arguments",
    "validate_schema_structure",
]

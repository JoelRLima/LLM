"""Planning-facing adapters over the shared argument-schema engine."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent.runtime.schema_validation import normalize_argument_schema, validate_schema_arguments


def validate_argument_shape(
    schema: Mapping[str, Any],
    properties: Mapping[str, Any],
    args: Mapping[str, Any],
    bound_fields: set[str] | None = None,
) -> None:
    # Older builtin skill descriptors expose a direct field-to-schema mapping
    # instead of JSON Schema's nested ``properties`` object.  Normalize that
    # representation at this planning adapter boundary so the shared engine
    # sees exactly the same shape as runtime invocation validation.
    effective_schema = schema
    if not isinstance(schema.get("properties"), Mapping) and properties:
        effective_schema = {
            **schema,
            "type": "object",
            "properties": dict(properties),
        }
    effective_schema = normalize_argument_schema(effective_schema)
    validate_schema_arguments(
        effective_schema,
        args,
        bound_fields=bound_fields,
        planning=True,
    )


def validate_property_value(key: str, value: Any, schema: Mapping[str, Any]) -> None:
    validate_schema_arguments(
        normalize_argument_schema({"type": "object", "properties": {key: schema}, "additionalProperties": False}),
        {key: value},
        planning=True,
    )


__all__ = ["validate_argument_shape", "validate_property_value"]

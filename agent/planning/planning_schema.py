"""Pure JSON-schema checks shared by planning-context callers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def validate_argument_shape(
    schema: Mapping[str, Any],
    properties: Mapping[str, Any],
    args: Mapping[str, Any],
    bound_fields: set[str] | None = None,
) -> None:
    bound_fields = bound_fields or set()
    unknown_bound = sorted(str(key) for key in bound_fields if key not in properties)
    if unknown_bound:
        raise ValueError(f"unknown bound argument(s): {', '.join(unknown_bound)}")
    if schema.get("additionalProperties") is False:
        unknown = sorted(str(key) for key in args if key not in properties)
        if unknown:
            raise ValueError(f"unknown argument(s): {', '.join(unknown)}")
    required = schema.get("required") or []
    required_values = required if isinstance(required, list) else [required]
    for key in required_values:
        if key in bound_fields:
            continue
        if key not in args:
            raise ValueError(f"missing required argument: {key}")


def validate_property_value(key: str, value: Any, schema: Mapping[str, Any]) -> None:
    expected_type = schema.get("type")
    valid_types = {
        "string": isinstance(value, str),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
    }
    if expected_type in valid_types and not valid_types[expected_type]:
        raise ValueError(f"argument '{key}' must be a {expected_type}")
    allowed_values = schema.get("enum")
    if isinstance(allowed_values, (list, tuple)) and value not in allowed_values:
        raise ValueError(f"argument '{key}' has an unsupported value")

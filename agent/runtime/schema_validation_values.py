"""Value-level rules for the shared argument-schema validator."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def _type_matches(value: Any, expected: str) -> bool:
    return {
        "string": isinstance(value, str),
        "integer": type(value) is int,
        "number": type(value) in {int, float},
        "boolean": type(value) is bool,
        "object": isinstance(value, Mapping),
        "array": isinstance(value, list),
        "null": value is None,
    }.get(expected, False)


def _unresolved(value: Any) -> bool:
    return (
        isinstance(value, Mapping)
        and any(key in value for key in ("from_step", "path", "binding"))
    ) or (isinstance(value, str) and value.startswith("${") and value.endswith("}"))


def _enum_contains(value: Any, allowed: tuple[Any, ...] | list[Any]) -> bool:
    # Python considers ``True == 1``. JSON values do not.
    for candidate in allowed:
        if type(candidate) is type(value) and candidate == value:
            return True
        if type(candidate) in {int, float} and type(value) in {int, float}:
            if candidate == value:
                return True
    return False


def validate_value(key: str, value: Any, schema: Any, *, planning: bool) -> None:
    if not isinstance(schema, Mapping):
        raise ValueError(f"schema for argument '{key}' must be an object")
    if _unresolved(value):
        if planning:
            return
        raise ValueError(f"argument '{key}' contains an unresolved binding")
    expected = schema.get("type")
    expected_types = (
        tuple(str(item) for item in expected)
        if isinstance(expected, (list, tuple))
        else (str(expected),) if expected is not None else ()
    )
    if expected_types and not any(_type_matches(value, item) for item in expected_types):
        raise ValueError(f"argument '{key}' has invalid type")
    enum = schema.get("enum")
    if isinstance(enum, (list, tuple)) and not _enum_contains(value, enum):
        raise ValueError(f"argument '{key}' has an unsupported value")
    _validate_scalar_limits(key, value, schema)
    if isinstance(value, list):
        _validate_array(key, value, schema, planning=planning)
    if isinstance(value, Mapping):
        _validate_object(key, value, schema, planning=planning)


def _validate_scalar_limits(key: str, value: Any, schema: Mapping[str, Any]) -> None:
    if type(value) in {int, float}:
        if schema.get("minimum") is not None and value < schema["minimum"]:
            raise ValueError(f"argument '{key}' is below minimum")
        if schema.get("maximum") is not None and value > schema["maximum"]:
            raise ValueError(f"argument '{key}' is above maximum")
    if isinstance(value, str):
        if schema.get("minLength") is not None and len(value) < schema["minLength"]:
            raise ValueError(f"argument '{key}' is shorter than minLength")
        if schema.get("maxLength") is not None and len(value) > schema["maxLength"]:
            raise ValueError(f"argument '{key}' is longer than maxLength")


def _validate_array(
    key: str, value: list[Any], schema: Mapping[str, Any], *, planning: bool,
) -> None:
    if schema.get("minItems") is not None and len(value) < schema["minItems"]:
        raise ValueError(f"argument '{key}' has too few items")
    if schema.get("maxItems") is not None and len(value) > schema["maxItems"]:
        raise ValueError(f"argument '{key}' has too many items")
    item_schema = schema.get("items")
    if item_schema is not None:
        for index, item in enumerate(value):
            validate_value(f"{key}[{index}]", item, item_schema, planning=planning)


def _validate_object(
    key: str, value: Mapping[str, Any], schema: Mapping[str, Any], *, planning: bool,
) -> None:
    nested_properties = schema.get("properties") or {}
    if not isinstance(nested_properties, Mapping):
        raise ValueError(f"schema for argument '{key}' must define object properties")
    if schema.get("additionalProperties") is False:
        unknown = sorted(str(item) for item in value if item not in nested_properties)
        if unknown:
            raise ValueError(f"argument '{key}' has unknown field(s): {', '.join(unknown)}")
    required = schema.get("required") or ()
    required_values = required if isinstance(required, (list, tuple)) else (required,)
    for required_key in required_values:
        if required_key not in value:
            raise ValueError(f"argument '{key}' is missing required field: {required_key}")
    for child_key, child_value in value.items():
        child_schema = nested_properties.get(child_key)
        if child_schema is not None:
            validate_value(f"{key}.{child_key}", child_value, child_schema, planning=planning)


__all__ = ["validate_value"]

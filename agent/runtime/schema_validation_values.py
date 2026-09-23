"""Value-level rules for the shared argument-schema validators."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any, cast


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


def valid_closed_schema_value(value: object, schema: object) -> bool:
    """Validate the closed JSON schema used by Engineering preflight."""

    if not isinstance(schema, Mapping) or not _closed_valid_enum(value, schema.get("enum")):
        return False
    validators = {
        "object": _closed_valid_object,
        "array": _closed_valid_array,
        "string": _closed_valid_string,
        "integer": _closed_valid_integer,
        "number": _closed_valid_number,
        "boolean": _closed_valid_boolean,
        "null": _closed_valid_null,
    }
    schema_type = schema.get("type")
    validator = validators.get(cast(str, schema_type))
    return isinstance(schema_type, str) and validator is not None and validator(value, schema)


def _closed_valid_enum(value: object, enum: object) -> bool:
    if enum is None:
        return True
    return isinstance(enum, (list, tuple)) and any(
        value == item and type(value) is type(item) for item in enum
    )


def _closed_valid_object(value: object, schema: Mapping[str, Any]) -> bool:
    if not isinstance(value, Mapping):
        return False
    properties = schema.get("properties", {})
    required = schema.get("required", ())
    if not isinstance(properties, Mapping) or not isinstance(required, (list, tuple)):
        return False
    if any(not isinstance(item, str) for item in required) or any(key not in value for key in required):
        return False
    if schema.get("additionalProperties") is False and any(key not in properties for key in value):
        return False
    return all(
        isinstance(key, str) and valid_closed_schema_value(item, properties.get(key, {}))
        for key, item in value.items()
    )


def _closed_valid_array(value: object, schema: Mapping[str, Any]) -> bool:
    if not isinstance(value, list) or not _closed_valid_array_bounds(value, schema):
        return False
    if schema.get("uniqueItems") is True and not _closed_valid_unique_items(value):
        return False
    item_schema = schema.get("items", {})
    return all(valid_closed_schema_value(item, item_schema) for item in value)


def _closed_valid_array_bounds(value: list[object], schema: Mapping[str, Any]) -> bool:
    minimum = schema.get("minItems")
    maximum = schema.get("maxItems")
    if isinstance(minimum, int) and not isinstance(minimum, bool) and len(value) < minimum:
        return False
    return not (isinstance(maximum, int) and not isinstance(maximum, bool) and len(value) > maximum)


def _closed_valid_unique_items(value: list[object]) -> bool:
    try:
        encoded = {
            json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
            for item in value
        }
    except (TypeError, ValueError):
        return False
    return len(encoded) == len(value)


def _closed_valid_string(value: object, schema: Mapping[str, Any]) -> bool:
    if not isinstance(value, str):
        return False
    minimum = schema.get("minLength")
    maximum = schema.get("maxLength")
    if isinstance(minimum, int) and not isinstance(minimum, bool) and len(value) < minimum:
        return False
    if isinstance(maximum, int) and not isinstance(maximum, bool) and len(value) > maximum:
        return False
    pattern = schema.get("pattern")
    return not pattern or (isinstance(pattern, str) and re.fullmatch(pattern, value) is not None)


def _closed_valid_integer(value: object, schema: Mapping[str, Any]) -> bool:
    return _closed_valid_number_type(value, schema, integer=True)


def _closed_valid_number(value: object, schema: Mapping[str, Any]) -> bool:
    return _closed_valid_number_type(value, schema, integer=False)


def _closed_valid_number_type(value: object, schema: Mapping[str, Any], *, integer: bool) -> bool:
    expected_type = int if integer else (int, float)
    if isinstance(value, bool) or not isinstance(value, expected_type):
        return False
    minimum = schema.get("minimum")
    maximum = schema.get("maximum")
    return not (
        isinstance(minimum, (int, float)) and value < minimum
        or isinstance(maximum, (int, float)) and value > maximum
    )


def _closed_valid_boolean(value: object, _schema: Mapping[str, Any]) -> bool:
    return isinstance(value, bool)


def _closed_valid_null(value: object, _schema: Mapping[str, Any]) -> bool:
    return value is None


__all__ = ["valid_closed_schema_value", "validate_value"]

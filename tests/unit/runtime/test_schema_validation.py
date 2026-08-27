from __future__ import annotations

import pytest

from agent.parsers import validate_tool_args
from agent.runtime.schema_validation import validate_schema_arguments
from agent.tools.invocation_support import validate_arguments

SCHEMA = {
    "type": "object",
    "properties": {
        "count": {"type": "integer", "minimum": 1, "maximum": 3},
        "ratio": {"type": "number"},
        "enabled": {"type": "boolean"},
        "mode": {"type": "string", "enum": ["safe", "fast"]},
    },
    "required": ["count", "enabled", "mode"],
    "additionalProperties": False,
}


def test_planning_and_concrete_schema_accept_the_same_concrete_arguments() -> None:
    args = {"count": 2, "ratio": 1.5, "enabled": False, "mode": "safe"}

    validate_schema_arguments(SCHEMA, args, planning=True)
    validate_schema_arguments(SCHEMA, args, planning=False)


@pytest.mark.parametrize(
    "args",
    [
        {"count": True, "enabled": False, "mode": "safe"},
        {"count": 0, "enabled": False, "mode": "safe"},
        {"count": 2, "enabled": False, "mode": "other"},
        {"count": 2, "enabled": False, "mode": "safe", "extra": 1},
    ],
)
def test_schema_rejects_bool_integer_bounds_enum_and_unknown_fields(args) -> None:
    with pytest.raises(ValueError):
        validate_schema_arguments(SCHEMA, args, planning=True)
    with pytest.raises(ValueError):
        validate_schema_arguments(SCHEMA, args, planning=False)


def test_bound_required_field_is_planning_only_and_concrete_bindings_are_rejected() -> None:
    planned = {"enabled": True, "mode": "safe"}
    validate_schema_arguments(
        SCHEMA,
        planned,
        bound_fields={"count"},
        planning=True,
    )
    with pytest.raises(ValueError):
        validate_schema_arguments(SCHEMA, planned, planning=False)
    with pytest.raises(ValueError):
        validate_schema_arguments(
            SCHEMA,
            {"count": "${step.1}", "enabled": True, "mode": "safe"},
            planning=False,
        )


def test_legacy_direct_field_schema_is_normalized_at_both_boundaries() -> None:
    legacy = {
        "file_path": "string: relative path",
        "start_line": "integer: optional line",
    }
    descriptor = type("Descriptor", (), {"schema": legacy})()

    validate_arguments(descriptor, {"file_path": "a.py", "start_line": 1})
    assert validate_tool_args(
        "legacy",
        {"file_path": "a.py", "start_line": 1},
        {"legacy": type("Skill", (), {"get_schema": lambda self: legacy})()},
    ) == (True, None)


def test_cross_field_validation_is_owned_by_operation_contract() -> None:
    calls: list[tuple[frozenset[str], bool]] = []

    class Contract:
        schema = {
            "type": "object",
            "properties": {"left": {"type": "integer"}, "right": {"type": "integer"}},
        }

        def validate_arguments(self, args, *, bound_fields=frozenset(), planning=False):
            calls.append((bound_fields, planning))
            if args.get("left") is not None and args.get("right") is not None and args["left"] > args["right"]:
                raise ValueError("left must not exceed right")

    contract = Contract()
    assert validate_tool_args("operation", {"left": 1, "right": 2}, {"operation": contract}) == (True, None)
    assert validate_tool_args("operation", {"left": 3, "right": 2}, {"operation": contract})[0] is False
    validate_arguments(contract, {"left": 1, "right": 2})
    assert calls == [(frozenset(), True), (frozenset(), True), (frozenset(), False)]

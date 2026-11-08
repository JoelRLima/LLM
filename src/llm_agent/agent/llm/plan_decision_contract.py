"""Structural admission helpers for executable plan slots."""

from __future__ import annotations

import re
from typing import Any

from llm_agent.agent.llm.plan_association_contract import (
    PLAN_ASSOCIATION_FIELDS,
    valid_plan_associations,
)

_MAX_PATH_INDEX = 1_000_000
_MAX_PATH_LENGTH = 32
_MAX_PATH_KEY = 128
_BINDING_TARGET = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def _is_integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_string(value: Any) -> bool:
    return isinstance(value, str)


def _is_non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _valid_path_segment(value: Any) -> bool:
    if _is_integer(value):
        return bool(0 <= value <= _MAX_PATH_INDEX)
    return bool(
        isinstance(value, str)
        and 0 < len(value) <= _MAX_PATH_KEY
        and not value.startswith("__")
        and all(ord(character) >= 32 for character in value)
    )


def valid_plan_bindings(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    for target, binding in value.items():
        if not isinstance(target, str) or not _BINDING_TARGET.fullmatch(target):
            return False
        if not isinstance(binding, dict) or set(binding) != {"from_step", "path"}:
            return False
        if not _is_integer(binding["from_step"]) or binding["from_step"] < 1:
            return False
        path = binding["path"]
        if not isinstance(path, list) or len(path) > _MAX_PATH_LENGTH:
            return False
        if not all(_valid_path_segment(segment) for segment in path):
            return False
    return True


def valid_tool_plan_step(value: Any, *, allow_associations: bool = True) -> bool:
    allowed = {"tool", "args", "bindings"}
    if allow_associations:
        allowed |= PLAN_ASSOCIATION_FIELDS
    if not isinstance(value, dict) or set(value) - allowed:
        return False
    if not _is_non_empty_string(value.get("tool")) or not isinstance(
        value.get("args"), dict
    ):
        return False
    return (
        ("bindings" not in value or valid_plan_bindings(value["bindings"]))
        and valid_plan_associations(value)
    )


def _valid_deferred_condition(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    required = {"kind", "observation_ref", "predicate", "on_true", "on_false"}
    if (
        set(value) - (required | PLAN_ASSOCIATION_FIELDS)
        or not required <= set(value)
        or value["kind"] != "deferred_condition"
        or not valid_plan_associations(value)
    ):
        return False
    if not _is_integer(value["observation_ref"]) or value["observation_ref"] < 1:
        return False
    predicate = value["predicate"]
    if (
        not isinstance(predicate, dict)
        or set(predicate) != {"op", "value"}
        or predicate["op"] != "equals"
        or not _is_string(predicate["value"])
    ):
        return False
    return value["on_false"] == {"waive_effect": "write"} and valid_tool_plan_step(
        value["on_true"], allow_associations=False
    )


def valid_plan_items(value: Any) -> bool:
    return isinstance(value, list) and all(
        _valid_deferred_condition(item) or valid_tool_plan_step(item) for item in value
    )


__all__ = ["valid_plan_bindings", "valid_plan_items", "valid_tool_plan_step"]

"""Shared strict scalar and collection validation for intent claims."""

from __future__ import annotations

from typing import Any, Mapping, cast

INTENT_CLAIM_SCHEMA_VERSION = "intent-claim-v1"
MAX_EFFECTS = 8
MAX_SELECTORS = 8
MAX_CONSTRAINTS = 8
MAX_EVIDENCE_SPANS = 16
MAX_ID_LENGTH = 64
MAX_VALUE_LENGTH = 512
MAX_EFFECT_LENGTH = 64
MAX_CONSTRAINT_VALUE_LENGTH = 512

_OPERATIONS = frozenset({"read", "plan", "do"})
_AMBIGUITIES = frozenset(
    {"none", "effect", "target", "constraint", "conflict", "grounding"}
)
_POLARITIES = frozenset({"requested", "prohibited"})
_SELECTOR_KINDS = frozenset({"path_literal", "symbol", "resource"})
_SELECTOR_ROLES = frozenset(
    {"source", "topic", "destination", "mutation_target", "memory", "unknown"}
)
_CONSTRAINT_KINDS = frozenset(
    {"prohibit_effect", "proposal_only", "require_validation", "preserve", "conditional"}
)


class IntentClaimError(ValueError):
    """Raised when a claim is malformed or cannot be bound safely."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise IntentClaimError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(raw: str) -> Any:
    del raw
    raise IntentClaimError("non-standard JSON number")


def _reject_surrogates(value: Any) -> None:
    if isinstance(value, str):
        if any(0xD800 <= ord(char) <= 0xDFFF for char in value):
            raise IntentClaimError("Unicode surrogate is not a scalar value")
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            _reject_surrogates(key)
            _reject_surrogates(item)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _reject_surrogates(item)


def _string(value: Any, field: str, *, max_length: int, non_empty: bool = True) -> str:
    if type(value) is not str:
        raise IntentClaimError(f"{field} must be a string")
    if len(value) > max_length or (non_empty and not value.strip()):
        raise IntentClaimError(f"{field} is outside its bound")
    return value


def _id(value: Any, field: str) -> str:
    return _string(value, field, max_length=MAX_ID_LENGTH)


def _integer(value: Any, field: str) -> int:
    if type(value) is not int:
        raise IntentClaimError(f"{field} must be an integer")
    return value


def _list(value: Any, field: str, maximum: int) -> list[Any] | tuple[Any, ...]:
    if type(value) not in {list, tuple}:
        raise IntentClaimError(f"{field} must be an array")
    if len(value) > maximum:
        raise IntentClaimError(f"{field} exceeds its bound")
    return cast(list[Any] | tuple[Any, ...], value)


def _string_list(value: Any, field: str, maximum: int) -> tuple[str, ...]:
    raw = _list(value, field, maximum)
    result = tuple(_id(item, f"{field}[]") for item in raw)
    if len(set(result)) != len(result):
        raise IntentClaimError(f"{field} contains duplicate ids")
    return result


def _exact_keys(value: Any, expected: set[str], field: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise IntentClaimError(f"{field} must be an object")
    if set(value) != expected:
        raise IntentClaimError(f"{field} keys are not exact")
    return value

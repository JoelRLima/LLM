"""Closed structural validation for plan association claims from the model."""

from __future__ import annotations

from typing import Any

PLAN_ASSOCIATION_FIELDS = frozenset({"requirement_ids", "obligation_ids"})
_MAX_ASSOCIATIONS = 16
_MAX_ASSOCIATION_ID_LENGTH = 128


def valid_plan_associations(value: dict[str, Any]) -> bool:
    """Validate optional non-empty, unique association lists on one plan slot."""

    return all(
        field not in value or _valid_ids(value[field])
        for field in PLAN_ASSOCIATION_FIELDS
    )


def _valid_ids(value: Any) -> bool:
    return (
        isinstance(value, list)
        and 1 <= len(value) <= _MAX_ASSOCIATIONS
        and all(
            isinstance(item, str)
            and bool(item.strip())
            and len(item) <= _MAX_ASSOCIATION_ID_LENGTH
            for item in value
        )
        and len(set(value)) == len(value)
    )


__all__ = ["PLAN_ASSOCIATION_FIELDS", "valid_plan_associations"]

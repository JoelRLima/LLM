"""Validation for non-authoritative requirement and obligation associations."""

from __future__ import annotations

from typing import Any

from llm_agent.agent.planning.plan_model_types import PlanDecodeError

_MAX_ASSOCIATIONS = 16
_MAX_ASSOCIATION_ID_LENGTH = 128


def decode_association_ids(value: Any, label: str) -> tuple[str, ...] | None:
    """Validate one optional list of bounded, unique association IDs."""

    if not isinstance(value, (list, tuple)) or not 1 <= len(value) <= _MAX_ASSOCIATIONS:
        raise PlanDecodeError(f"{label} deve ser uma lista de 1 a 16 IDs")
    if any(
        not isinstance(item, str)
        or not item.strip()
        or len(item) > _MAX_ASSOCIATION_ID_LENGTH
        for item in value
    ):
        raise PlanDecodeError(f"{label} contem ID vazio, nao textual ou grande demais")
    if len(set(value)) != len(value):
        raise PlanDecodeError(f"{label} nao pode conter IDs duplicados")
    return tuple(value)


__all__ = ["decode_association_ids"]

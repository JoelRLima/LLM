"""Validation for descriptor-owned public invocation fields."""

from __future__ import annotations

from typing import Any


def normalize_public_invocation_fields(value: Any) -> frozenset[str]:
    if isinstance(value, str):
        raise TypeError("public_invocation_fields deve ser uma colecao de strings")
    try:
        fields = frozenset(value)
    except TypeError as exc:
        raise TypeError("public_invocation_fields deve ser uma colecao de strings") from exc
    if any(type(item) is not str or not item.strip() or item != item.strip() for item in fields):
        raise ValueError("public_invocation_fields contem valor invalido")
    return fields

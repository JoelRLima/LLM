"""Shared bounded normalization for durable task-semantic values."""

from __future__ import annotations

import re
from typing import Any

MAX_OBLIGATION_TEXT = 240
MAX_OBLIGATION_TARGET = 256
MAX_OBLIGATION_OPERANDS = 4
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,63}$")


class TaskSemanticsError(ValueError):
    """Raised when a task semantic contract cannot be accepted safely."""


def _normalize_text(text: str) -> str:
    import unicodedata

    folded = unicodedata.normalize("NFKD", text.casefold())
    return "".join(char for char in folded if not unicodedata.combining(char))


def _normalize_effect(effect: Any) -> str:
    if not isinstance(effect, str):
        raise TaskSemanticsError("efeito deve ser textual")
    value = effect.strip().casefold()
    if not value or not _ID_RE.fullmatch(value):
        raise TaskSemanticsError("efeito invalido")
    return value


def _normalize_kind(kind: Any) -> str:
    if not isinstance(kind, str):
        raise TaskSemanticsError("kind da obrigacao deve ser textual")
    value = kind.strip().casefold()
    if not value or not _ID_RE.fullmatch(value):
        raise TaskSemanticsError("kind da obrigacao invalido")
    return value


def _normalize_id(value: Any) -> str:
    if not isinstance(value, str):
        raise TaskSemanticsError("id da obrigacao deve ser textual")
    normalized = value.strip().casefold()
    if not _ID_RE.fullmatch(normalized):
        raise TaskSemanticsError("id da obrigacao invalido")
    return normalized


def _normalize_description(value: Any) -> str:
    if not isinstance(value, str):
        raise TaskSemanticsError("descricao da obrigacao deve ser textual")
    normalized = " ".join(value.split())
    if not normalized or len(normalized) > MAX_OBLIGATION_TEXT:
        raise TaskSemanticsError("descricao da obrigacao invalida")
    return normalized


def _normalize_optional_text(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TaskSemanticsError("condition da obrigacao deve ser textual")
    normalized = " ".join(value.split())
    if not normalized or len(normalized) > MAX_OBLIGATION_TEXT:
        raise TaskSemanticsError("condition da obrigacao invalida")
    return normalized


def _normalize_optional_identity(value: Any, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TaskSemanticsError(f"{field} da obrigacao deve ser textual")
    normalized = " ".join(value.split())
    if not normalized or len(normalized) > MAX_OBLIGATION_TARGET:
        raise TaskSemanticsError(f"{field} da obrigacao invalido")
    return normalized


def _normalize_operands(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)) or len(value) > MAX_OBLIGATION_OPERANDS:
        raise TaskSemanticsError("operands da obrigacao invalidos")
    operands = tuple(_normalize_optional_identity(item, "operando") for item in value)
    if any(item is None for item in operands):
        raise TaskSemanticsError("operands da obrigacao invalidos")
    return tuple(item for item in operands if item is not None)


def _normalize_query_source(value: Any) -> str | None:
    if value is None:
        return None
    if value != "previous_read":
        raise TaskSemanticsError("query_source da obrigacao invalido")
    return "previous_read"


def _eligible_evidence_ref(value: Any) -> int | str:
    if isinstance(value, bool):
        raise TaskSemanticsError("referencia de evidencia invalida")
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, str) and value.strip() and len(value.strip()) <= 128:
        return value.strip()
    raise TaskSemanticsError("transicao operacional requer referencia de evidencia")


__all__ = [
    "MAX_OBLIGATION_OPERANDS",
    "MAX_OBLIGATION_TARGET",
    "MAX_OBLIGATION_TEXT",
    "TaskSemanticsError",
]

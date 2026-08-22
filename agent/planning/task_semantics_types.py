"""Typed values and bounded normalization for canonical task semantics."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import Enum
from typing import Any

MAX_OBLIGATIONS = 16
MAX_REVIEW_OBLIGATIONS = 8
MAX_OBLIGATION_TEXT = 240

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,63}$")


class TaskSemanticsError(ValueError):
    """Raised when a task semantic contract cannot be accepted safely."""


class ObligationStatus(str, Enum):
    PENDING = "pending"
    SATISFIED = "satisfied"
    WAIVED = "waived"
    BLOCKED = "blocked"


def _normalize_text(text: str) -> str:
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


def _eligible_evidence_ref(value: Any) -> int | str:
    if isinstance(value, bool):
        raise TaskSemanticsError("referencia de evidencia invalida")
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, str) and value.strip() and len(value.strip()) <= 128:
        return value.strip()
    raise TaskSemanticsError("transicao operacional requer referencia de evidencia")


@dataclass(frozen=True, slots=True)
class EffectSemantics:
    requested: tuple[str, ...] = ()
    prohibited: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TaskIntent:
    """Immutable task-level intent; it is never a terminal status."""

    original_objective: str
    requested_effects: tuple[str, ...] = ()
    prohibited_effects: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.original_objective, str):
            raise TaskSemanticsError("objetivo original invalido")
        object.__setattr__(
            self,
            "requested_effects",
            tuple(dict.fromkeys(_normalize_effect(item) for item in self.requested_effects)),
        )
        object.__setattr__(
            self,
            "prohibited_effects",
            tuple(dict.fromkeys(_normalize_effect(item) for item in self.prohibited_effects)),
        )

    @property
    def objective(self) -> str:
        return self.original_objective


@dataclass(frozen=True, slots=True)
class TaskObligation:
    """Durable requirement definition, separate from a replaceable plan step."""

    id: str
    kind: str
    description: str
    effect: str | None = None
    condition: str | None = None

    def __post_init__(self) -> None:
        normalized_id = _normalize_id(self.id)
        normalized_kind = _normalize_kind(self.kind)
        effect = _normalize_effect(self.effect) if self.effect is not None else None
        if normalized_kind == "effect" and effect is None:
            raise TaskSemanticsError("obrigacao de efeito requer effect")
        if normalized_kind != "effect" and effect is not None:
            raise TaskSemanticsError("effect so pode ser usado por obrigacao de efeito")
        object.__setattr__(self, "id", normalized_id)
        object.__setattr__(self, "kind", normalized_kind)
        object.__setattr__(self, "description", _normalize_description(self.description))
        object.__setattr__(self, "effect", effect)
        object.__setattr__(self, "condition", _normalize_optional_text(self.condition))


__all__ = (
    "EffectSemantics",
    "MAX_OBLIGATIONS",
    "MAX_OBLIGATION_TEXT",
    "MAX_REVIEW_OBLIGATIONS",
    "ObligationStatus",
    "TaskIntent",
    "TaskObligation",
    "TaskSemanticsError",
)

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
MAX_OBLIGATION_TARGET = 256
MAX_OBLIGATION_OPERANDS = 4

# This is deliberately a closed set.  A model may describe a requirement only
# when the runtime has a bounded transition and evidence matcher for it.
OBLIGATION_KINDS = frozenset({"read", "search", "compare", "analyze", "effect", "fallback"})

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
    operands = tuple(
        _normalize_optional_identity(item, "operando")
        for item in value
    )
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
    target: str | None = None
    query: str | None = None
    operands: tuple[str, ...] = ()
    fallback_target: str | None = None
    query_source: str | None = None

    def __post_init__(self) -> None:
        normalized_id = _normalize_id(self.id)
        normalized_kind = _normalize_kind(self.kind)
        if normalized_kind not in OBLIGATION_KINDS:
            raise TaskSemanticsError("kind da obrigacao nao suportado")
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
        object.__setattr__(self, "target", _normalize_optional_identity(self.target, "target"))
        object.__setattr__(self, "query", _normalize_optional_identity(self.query, "query"))
        object.__setattr__(self, "operands", _normalize_operands(self.operands))
        object.__setattr__(
            self,
            "fallback_target",
            _normalize_optional_identity(self.fallback_target, "fallback_target"),
        )
        object.__setattr__(self, "query_source", _normalize_query_source(self.query_source))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "description": self.description,
            **({"effect": self.effect} if self.effect is not None else {}),
            **({"condition": self.condition} if self.condition is not None else {}),
            **({"target": self.target} if self.target is not None else {}),
            **({"query": self.query} if self.query is not None else {}),
            **({"operands": list(self.operands)} if self.operands else {}),
            **({"fallback_target": self.fallback_target} if self.fallback_target is not None else {}),
            **({"query_source": self.query_source} if self.query_source is not None else {}),
        }


def _validate_effect_obligation(item: TaskObligation) -> None:
    if any(value for value in (item.target, item.query, item.operands, item.fallback_target, item.query_source)):
        raise TaskSemanticsError("obrigacao de efeito contem identidade invalida")


def _validate_read_obligation(item: TaskObligation) -> None:
    if item.target is None or any(
        value for value in (item.query, item.operands, item.fallback_target, item.query_source)
    ):
        raise TaskSemanticsError("obrigacao read requer target exclusivo")


def _validate_search_obligation(item: TaskObligation) -> None:
    if (item.query is None) == (item.query_source is None):
        raise TaskSemanticsError("obrigacao search requer query ou query_source exclusivo")
    if item.operands or item.fallback_target is not None:
        raise TaskSemanticsError("obrigacao search contem identidade invalida")


def _validate_compare_obligation(item: TaskObligation) -> None:
    if len(item.operands) != 2 or any(
        value is not None for value in (item.target, item.query, item.fallback_target, item.query_source)
    ):
        raise TaskSemanticsError("obrigacao compare requer exatamente dois operands")


def _validate_analyze_obligation(item: TaskObligation) -> None:
    if item.target is None and item.query is None:
        raise TaskSemanticsError("obrigacao analyze requer target ou query")
    if item.operands or item.fallback_target is not None or item.query_source is not None:
        raise TaskSemanticsError("obrigacao analyze contem identidade invalida")


def _validate_fallback_obligation(item: TaskObligation) -> None:
    if (
        item.fallback_target is None
        or item.target is not None
        or item.query is not None
        or item.operands
        or item.query_source is not None
    ):
        raise TaskSemanticsError("obrigacao fallback requer fallback_target exclusivo")


def validate_closed_obligation(item: TaskObligation) -> None:
    """Reject forms for which this runtime has no bounded transition."""

    if not isinstance(item, TaskObligation):
        raise TaskSemanticsError("obrigacao invalida")
    validators = {
        "effect": _validate_effect_obligation,
        "read": _validate_read_obligation,
        "search": _validate_search_obligation,
        "compare": _validate_compare_obligation,
        "analyze": _validate_analyze_obligation,
        "fallback": _validate_fallback_obligation,
    }
    validator = validators.get(item.kind)
    if validator is None:
        raise TaskSemanticsError("kind da obrigacao nao suportado")
    validator(item)


__all__ = (
    "EffectSemantics",
    "MAX_OBLIGATIONS",
    "MAX_OBLIGATION_TEXT",
    "MAX_OBLIGATION_TARGET",
    "MAX_OBLIGATION_OPERANDS",
    "MAX_REVIEW_OBLIGATIONS",
    "OBLIGATION_KINDS",
    "ObligationStatus",
    "TaskIntent",
    "TaskObligation",
    "TaskSemanticsError",
    "validate_closed_obligation",
)

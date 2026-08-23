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


class AdmissionSource(str, Enum):
    """Trusted origin of a durable task obligation."""

    OBJECTIVE_DERIVED = "OBJECTIVE_DERIVED"
    CANONICAL_EVIDENCE_DERIVED = "CANONICAL_EVIDENCE_DERIVED"
    SAFETY_REQUIRED = "SAFETY_REQUIRED"
    EXTERNALLY_AUTHORIZED = "EXTERNALLY_AUTHORIZED"


ObligationAdmissionSource = AdmissionSource


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


def _normalize_admission_source(value: Any) -> AdmissionSource:
    if isinstance(value, AdmissionSource):
        return value
    if not isinstance(value, str):
        raise TaskSemanticsError("origem de admissao da obrigacao invalida")
    try:
        return AdmissionSource(value.strip().upper())
    except ValueError as exc:
        raise TaskSemanticsError("origem de admissao da obrigacao invalida") from exc


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
    admission_source: AdmissionSource = AdmissionSource.OBJECTIVE_DERIVED
    admission_evidence_ref: int | str | None = None
    admission_authorization: str | None = None

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
        from agent.planning.task_semantics_obligation import normalize_admission_fields

        admission_source, admission_evidence_ref, admission_authorization = normalize_admission_fields(
            self.admission_source,
            self.admission_evidence_ref,
            self.admission_authorization,
        )
        object.__setattr__(self, "admission_source", admission_source)
        object.__setattr__(self, "admission_evidence_ref", admission_evidence_ref)
        object.__setattr__(self, "admission_authorization", admission_authorization)

    def to_dict(self) -> dict[str, Any]:
        from agent.planning.task_semantics_obligation import obligation_to_dict

        return obligation_to_dict(self)


def validate_closed_obligation(item: TaskObligation) -> None:
    from agent.planning.task_semantics_obligation import validate_closed_obligation as validate

    validate(item)


__all__ = (
    "AdmissionSource",
    "EffectSemantics",
    "MAX_OBLIGATIONS",
    "MAX_OBLIGATION_TEXT",
    "MAX_OBLIGATION_TARGET",
    "MAX_OBLIGATION_OPERANDS",
    "MAX_REVIEW_OBLIGATIONS",
    "OBLIGATION_KINDS",
    "ObligationStatus",
    "ObligationAdmissionSource",
    "TaskIntent",
    "TaskObligation",
    "TaskSemanticsError",
    "validate_closed_obligation",
)

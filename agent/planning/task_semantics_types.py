"""Typed values and bounded normalization for canonical task semantics."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from agent.planning.task_semantics_normalization import (
    MAX_OBLIGATION_OPERANDS,
    MAX_OBLIGATION_TARGET,
    MAX_OBLIGATION_TEXT,
    TaskSemanticsError,
    _eligible_evidence_ref,
    _normalize_description,
    _normalize_effect,
    _normalize_id,
    _normalize_kind,
    _normalize_operands,
    _normalize_optional_identity,
    _normalize_optional_text,
    _normalize_query_source,
    _normalize_text,
)
from agent.resources.contracts import normalize_resource_id

MAX_OBLIGATIONS = 16
MAX_REVIEW_OBLIGATIONS = 8
# This is deliberately a closed set.  A model may describe a requirement only
# when the runtime has a bounded transition and evidence matcher for it.
OBLIGATION_KINDS = frozenset({"read", "search", "compare", "analyze", "effect", "fallback"})

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


class PredicateResolutionState(str, Enum):
    """Trusted runtime state of a bounded conditional predicate."""

    TRUE = "TRUE"
    FALSE = "FALSE"
    UNRESOLVED = "UNRESOLVED"


_PREDICATE_PROVENANCES = frozenset(
    {"workspace_observation", "runtime_observation", "deterministic_fact"}
)


def _normalize_predicate_id(value: Any) -> str:
    if not isinstance(value, str):
        raise TaskSemanticsError("identidade de predicate invalida")
    normalized = " ".join(value.casefold().split())
    if not normalized or len(normalized) > MAX_OBLIGATION_TEXT:
        raise TaskSemanticsError("identidade de predicate invalida")
    return normalized


@dataclass(frozen=True, slots=True)
class PredicateEvidence:
    """A trusted, replayable fact resolving one predicate."""

    predicate_id: str
    state: PredicateResolutionState
    evidence_ref: int | str
    provenance: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "predicate_id", _normalize_predicate_id(self.predicate_id))
        raw_state = self.state
        if not isinstance(raw_state, PredicateResolutionState):
            try:
                raw_state = PredicateResolutionState(str(raw_state).strip().upper())
            except ValueError as exc:
                raise TaskSemanticsError("estado de predicate invalido") from exc
        if raw_state is PredicateResolutionState.UNRESOLVED:
            raise TaskSemanticsError("evidencia de predicate deve resolver TRUE/FALSE")
        object.__setattr__(self, "state", raw_state)
        if isinstance(self.evidence_ref, bool) or (
            not isinstance(self.evidence_ref, (int, str))
            or (isinstance(self.evidence_ref, int) and self.evidence_ref <= 0)
            or (isinstance(self.evidence_ref, str) and not self.evidence_ref.strip())
        ):
            raise TaskSemanticsError("referencia de evidencia do predicate invalida")
        provenance = str(self.provenance).strip().casefold()
        if provenance not in _PREDICATE_PROVENANCES:
            raise TaskSemanticsError("proveniencia de predicate nao confiavel")
        object.__setattr__(self, "provenance", provenance)

    @property
    def value(self) -> bool:
        return self.state is PredicateResolutionState.TRUE


def _normalize_admission_source(value: Any) -> AdmissionSource:
    if isinstance(value, AdmissionSource):
        return value
    if not isinstance(value, str):
        raise TaskSemanticsError("origem de admissao da obrigacao invalida")
    try:
        return AdmissionSource(value.strip().upper())
    except ValueError as exc:
        raise TaskSemanticsError("origem de admissao da obrigacao invalida") from exc


@dataclass(frozen=True, slots=True)
class EffectIntent:
    """One target-aware, polarity-aware effect claim from task intent."""

    effect: str
    target: str = "*"
    polarity: str = "requested"
    condition: str | None = None
    source: str = "objective"
    predicate_id: str | None = field(default=None, kw_only=True)
    predicate_expected: bool | None = field(default=None, kw_only=True)
    predicate_state: PredicateResolutionState = field(
        default=PredicateResolutionState.UNRESOLVED,
        kw_only=True,
    )
    predicate_evidence_ref: int | str | None = field(default=None, kw_only=True)
    predicate_provenance: str | None = field(default=None, kw_only=True)
    # These fields describe the parser's candidate classification only.  They
    # are deliberately not authority decisions; the admission kernel must
    # still prove the complete positive construction before a durable effect
    # can enter TaskIntent.
    candidate_role: str = field(default="UNKNOWN", kw_only=True)
    positive_syntax: bool = field(default=False, kw_only=True)

    def __post_init__(self) -> None:
        object.__setattr__(self, "effect", _normalize_effect(self.effect))
        object.__setattr__(self, "target", normalize_resource_id(self.target))
        polarity = str(self.polarity).strip().casefold()
        if polarity not in {"requested", "prohibited"}:
            raise TaskSemanticsError("polaridade do efeito invalida")
        object.__setattr__(self, "polarity", polarity)
        object.__setattr__(self, "condition", _normalize_optional_text(self.condition))
        source = str(self.source).strip()
        if not source or len(source) > MAX_OBLIGATION_TARGET:
            raise TaskSemanticsError("origem do efeito invalida")
        object.__setattr__(self, "source", source)
        candidate_role = str(self.candidate_role).strip().upper()
        if candidate_role not in {
            "SOURCE",
            "TOPIC",
            "DESTINATION",
            "MUTATION_TARGET",
            "MEMORY",
            "UNKNOWN",
        }:
            raise TaskSemanticsError("papel candidato do efeito invalido")
        if type(self.positive_syntax) is not bool:
            raise TaskSemanticsError("classificacao positiva do efeito invalida")
        object.__setattr__(self, "candidate_role", candidate_role)
        raw_state = self.predicate_state
        if not isinstance(raw_state, PredicateResolutionState):
            try:
                raw_state = PredicateResolutionState(str(raw_state).strip().upper())
            except ValueError as exc:
                raise TaskSemanticsError("estado de predicate invalido") from exc
        predicate_id = (
            _normalize_predicate_id(self.predicate_id)
            if self.predicate_id is not None
            else None
        )
        if predicate_id is None and (
            self.predicate_expected is not None
            or self.predicate_evidence_ref is not None
            or self.predicate_provenance is not None
            or raw_state is not PredicateResolutionState.UNRESOLVED
        ):
            raise TaskSemanticsError("estado de predicate sem identidade")
        if self.predicate_expected is not None and type(self.predicate_expected) is not bool:
            raise TaskSemanticsError("valor esperado do predicate invalido")
        if predicate_id is not None and self.predicate_expected is None:
            raise TaskSemanticsError("predicate requer valor esperado")
        if raw_state is PredicateResolutionState.UNRESOLVED:
            if self.predicate_evidence_ref is not None or self.predicate_provenance is not None:
                raise TaskSemanticsError("predicate unresolved nao pode conter evidencia")
        else:
            if predicate_id is None or self.predicate_evidence_ref is None or self.predicate_provenance is None:
                raise TaskSemanticsError("predicate resolvido requer evidencia confiavel")
            provenance = str(self.predicate_provenance).strip().casefold()
            if provenance not in _PREDICATE_PROVENANCES:
                raise TaskSemanticsError("proveniencia de predicate nao confiavel")
            object.__setattr__(self, "predicate_provenance", provenance)
            if isinstance(self.predicate_evidence_ref, bool) or not isinstance(
                self.predicate_evidence_ref, (int, str)
            ) or (
                isinstance(self.predicate_evidence_ref, int)
                and self.predicate_evidence_ref <= 0
            ) or (
                isinstance(self.predicate_evidence_ref, str)
                and not self.predicate_evidence_ref.strip()
            ):
                raise TaskSemanticsError("referencia de evidencia do predicate invalida")
        object.__setattr__(self, "predicate_id", predicate_id)
        object.__setattr__(self, "predicate_state", raw_state)

    @property
    def kind(self) -> str:
        return self.effect

    @property
    def prohibited(self) -> bool:
        return self.polarity == "prohibited"

    @property
    def predicate_identity(self) -> str | None:
        return self.predicate_id

    @property
    def expected_predicate_value(self) -> bool | None:
        return self.predicate_expected

    @property
    def predicate_resolution(self) -> PredicateResolutionState:
        return self.predicate_state

    @property
    def conditional(self) -> bool:
        return self.predicate_id is not None


@dataclass(frozen=True, slots=True)
class EffectSemantics:
    requested: tuple[str, ...] = ()
    prohibited: tuple[str, ...] = ()
    intents: tuple[EffectIntent, ...] = ()
    proposal_only: bool = False

    @property
    def requested_intents(self) -> tuple[EffectIntent, ...]:
        return tuple(item for item in self.intents if item.polarity == "requested")

    @property
    def prohibited_intents(self) -> tuple[EffectIntent, ...]:
        return tuple(item for item in self.intents if item.polarity == "prohibited")

    @property
    def effect_intents(self) -> tuple[EffectIntent, ...]:
        return self.intents


@dataclass(frozen=True, slots=True)
class TaskIntent:
    """Immutable task-level intent; it is never a terminal status."""

    original_objective: str
    requested_effects: tuple[str, ...] = ()
    prohibited_effects: tuple[str, ...] = ()
    effect_intents: tuple[EffectIntent, ...] = field(default_factory=tuple, kw_only=True)

    def __post_init__(self) -> None:
        if not isinstance(self.original_objective, str):
            raise TaskSemanticsError("objetivo original invalido")
        requested = list(
            dict.fromkeys(_normalize_effect(item) for item in self.requested_effects)
        )
        prohibited = tuple(
            dict.fromkeys(_normalize_effect(item) for item in self.prohibited_effects)
        )
        raw_intents = tuple(self.effect_intents or ())
        if raw_intents and any(not isinstance(item, EffectIntent) for item in raw_intents):
            raise TaskSemanticsError("effect_intents invalidos")
        intents = raw_intents or tuple(
            [*(EffectIntent(effect) for effect in requested),
             *(EffectIntent(effect, polarity="prohibited") for effect in prohibited)]
        )
        prohibited_list = list(prohibited)
        for item in intents:
            target = requested if item.polarity == "requested" else prohibited_list
            if item.effect not in target:
                target.append(item.effect)
        object.__setattr__(self, "requested_effects", tuple(requested))
        object.__setattr__(self, "prohibited_effects", tuple(prohibited_list))
        object.__setattr__(self, "effect_intents", tuple(dict.fromkeys(intents)))

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
    "EffectIntent",
    "EffectSemantics",
    "MAX_OBLIGATIONS",
    "MAX_OBLIGATION_TEXT",
    "MAX_OBLIGATION_TARGET",
    "MAX_OBLIGATION_OPERANDS",
    "MAX_REVIEW_OBLIGATIONS",
    "OBLIGATION_KINDS",
    "ObligationStatus",
    "ObligationAdmissionSource",
    "PredicateEvidence",
    "PredicateResolutionState",
    "TaskIntent",
    "TaskObligation",
    "TaskSemanticsError",
    "_eligible_evidence_ref",
    "_normalize_text",
    "validate_closed_obligation",
)

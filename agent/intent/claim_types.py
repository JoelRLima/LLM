"""Immutable typed projections for the untrusted intent-claim wire object."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

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
_AMBIGUITIES = frozenset({"none", "effect", "target", "constraint", "conflict", "grounding"})
_POLARITIES = frozenset({"requested", "prohibited"})
_SELECTOR_KINDS = frozenset({"path_literal", "symbol", "resource"})
_SELECTOR_ROLES = frozenset({"source", "topic", "destination", "mutation_target", "memory", "unknown"})
_CONSTRAINT_KINDS = frozenset({"prohibit_effect", "proposal_only", "require_validation", "preserve", "conditional"})


class IntentClaimError(ValueError):
    """Raised when a claim is malformed or cannot be bound safely."""


def _list(value: Any, field: str, maximum: int) -> list[Any] | tuple[Any, ...]:
    if type(value) not in {list, tuple}:
        raise IntentClaimError(f"{field} must be an array")
    if len(value) > maximum:
        raise IntentClaimError(f"{field} exceeds its bound")
    return cast(list[Any] | tuple[Any, ...], value)


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


def _string_list(value: Any, field: str, maximum: int) -> tuple[str, ...]:
    raw = _list(value, field, maximum)
    result = tuple(_id(item, f"{field}[]") for item in raw)
    if len(set(result)) != len(result):
        raise IntentClaimError(f"{field} contains duplicate ids")
    return result


def _set_ids(
    owner: Any,
    field: str,
    value: Any,
    maximum: int,
    *,
    required_error: str | None = None,
) -> None:
    result = _string_list(value, field, maximum)
    if required_error is not None and not result:
        raise IntentClaimError(required_error)
    object.__setattr__(owner, field, result)


@dataclass(frozen=True, slots=True)
class EvidenceSpan:
    """Untrusted span coordinates emitted by the semantic interpreter."""

    span_id: str
    start: int
    end: int
    text: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "span_id", _id(self.span_id, "span_id"))
        object.__setattr__(self, "start", _integer(self.start, "start"))
        object.__setattr__(self, "end", _integer(self.end, "end"))
        object.__setattr__(self, "text", _string(self.text, "text", max_length=MAX_VALUE_LENGTH))
        if self.start < 0 or self.start >= self.end:
            raise IntentClaimError("evidence span bounds are invalid")


@dataclass(frozen=True, slots=True)
class EffectClaim:
    """An untrusted requested/prohibited durable-effect claim."""

    effect: str
    polarity: str
    selector_ids: tuple[str, ...]
    evidence_span_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "effect", _string(self.effect, "effect", max_length=MAX_EFFECT_LENGTH)
        )
        object.__setattr__(self, "polarity", _string(self.polarity, "polarity", max_length=16))
        if self.polarity not in _POLARITIES:
            raise IntentClaimError("effect polarity is invalid")
        _set_ids(self, "selector_ids", self.selector_ids, MAX_SELECTORS)
        _set_ids(
            self,
            "evidence_span_ids",
            self.evidence_span_ids,
            MAX_EVIDENCE_SPANS,
            required_error="effect claim requires evidence",
        )


@dataclass(frozen=True, slots=True)
class TargetSelectorClaim:
    """An untrusted literal, symbolic, or logical-resource selector."""

    selector_id: str
    kind: str
    value: str
    role: str
    evidence_span_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "selector_id", _id(self.selector_id, "selector_id"))
        object.__setattr__(self, "kind", _string(self.kind, "kind", max_length=32))
        object.__setattr__(self, "value", _string(self.value, "value", max_length=MAX_VALUE_LENGTH))
        object.__setattr__(self, "role", _string(self.role, "role", max_length=32))
        if self.kind not in _SELECTOR_KINDS:
            raise IntentClaimError("selector kind is invalid")
        if self.role not in _SELECTOR_ROLES:
            raise IntentClaimError("selector role is invalid")
        _set_ids(
            self,
            "evidence_span_ids",
            self.evidence_span_ids,
            MAX_EVIDENCE_SPANS,
            required_error="selector claim requires evidence",
        )


@dataclass(frozen=True, slots=True)
class ConstraintClaim:
    """Small closed constraint vocabulary; never executable policy text."""

    kind: str
    evidence_span_ids: tuple[str, ...]
    value: str | None = None
    selector_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", _string(self.kind, "kind", max_length=32))
        if self.kind not in _CONSTRAINT_KINDS:
            raise IntentClaimError("constraint kind is unsupported")
        if self.value is not None:
            object.__setattr__(
                self,
                "value",
                _string(self.value, "constraint.value", max_length=MAX_CONSTRAINT_VALUE_LENGTH),
            )
        _set_ids(self, "selector_ids", self.selector_ids, MAX_SELECTORS)
        _set_ids(
            self,
            "evidence_span_ids",
            self.evidence_span_ids,
            MAX_EVIDENCE_SPANS,
            required_error="constraint claim requires evidence",
        )


@dataclass(frozen=True, slots=True)
class IntentClaimV1:
    """Versioned, immutable, explicitly untrusted semantic interpretation."""

    operation: str
    ambiguity: str
    effects: tuple[EffectClaim, ...] = ()
    selectors: tuple[TargetSelectorClaim, ...] = ()
    constraints: tuple[ConstraintClaim, ...] = ()
    evidence_spans: tuple[EvidenceSpan, ...] = ()
    schema_version: str = INTENT_CLAIM_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != INTENT_CLAIM_SCHEMA_VERSION:
            raise IntentClaimError("intent claim schema version is invalid")
        object.__setattr__(self, "operation", _string(self.operation, "operation", max_length=16))
        object.__setattr__(self, "ambiguity", _string(self.ambiguity, "ambiguity", max_length=32))
        if self.operation not in _OPERATIONS:
            raise IntentClaimError("operation is invalid")
        if self.ambiguity not in _AMBIGUITIES:
            raise IntentClaimError("ambiguity is invalid")
        for field_name, values, maximum, expected in (
            ("effects", self.effects, MAX_EFFECTS, EffectClaim),
            ("selectors", self.selectors, MAX_SELECTORS, TargetSelectorClaim),
            ("constraints", self.constraints, MAX_CONSTRAINTS, ConstraintClaim),
            ("evidence_spans", self.evidence_spans, MAX_EVIDENCE_SPANS, EvidenceSpan),
        ):
            values_tuple = tuple(values)
            if len(values_tuple) > maximum or any(not isinstance(item, expected) for item in values_tuple):
                raise IntentClaimError(f"{field_name} is invalid or exceeds its bound")
            object.__setattr__(self, field_name, values_tuple)
        self._validate_references()

    def _validate_references(self) -> None:
        span_ids = [item.span_id for item in self.evidence_spans]
        selector_ids = [item.selector_id for item in self.selectors]
        if len(set(span_ids)) != len(span_ids):
            raise IntentClaimError("duplicate evidence span id")
        if len(set(selector_ids)) != len(selector_ids):
            raise IntentClaimError("duplicate selector id")
        known_spans = set(span_ids)
        known_selectors = set(selector_ids)
        for effect_item in self.effects:
            self._validate_item_references(effect_item.evidence_span_ids, effect_item.selector_ids, known_spans, known_selectors)
        for selector_item in self.selectors:
            self._validate_item_references(selector_item.evidence_span_ids, (), known_spans, known_selectors)
        for constraint_item in self.constraints:
            self._validate_item_references(constraint_item.evidence_span_ids, constraint_item.selector_ids, known_spans, known_selectors)

    @staticmethod
    def _validate_item_references(
        evidence_span_ids: tuple[str, ...],
        selector_ids: tuple[str, ...],
        known_spans: set[str],
        known_selectors: set[str],
    ) -> None:
        if any(ref not in known_spans for ref in evidence_span_ids):
            raise IntentClaimError("claim references a missing evidence span")
        if any(ref not in known_selectors for ref in selector_ids):
            raise IntentClaimError("claim references a missing selector")

    @property
    def untrusted(self) -> bool:
        """Make the trust boundary explicit to callers and reports."""

        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "operation": self.operation,
            "ambiguity": self.ambiguity,
            "effects": [
                {
                    "effect": item.effect,
                    "polarity": item.polarity,
                    "selector_ids": list(item.selector_ids),
                    "evidence_span_ids": list(item.evidence_span_ids),
                }
                for item in self.effects
            ],
            "selectors": [
                {
                    "selector_id": item.selector_id,
                    "kind": item.kind,
                    "value": item.value,
                    "role": item.role,
                    "evidence_span_ids": list(item.evidence_span_ids),
                }
                for item in self.selectors
            ],
            "constraints": [
                {
                    "kind": item.kind,
                    **({"value": item.value} if item.value is not None else {}),
                    **({"selector_ids": list(item.selector_ids)} if item.selector_ids else {}),
                    "evidence_span_ids": list(item.evidence_span_ids),
                }
                for item in self.constraints
            ],
            "evidence_spans": [
                {
                    "span_id": item.span_id,
                    "start": item.start,
                    "end": item.end,
                    "text": item.text,
                }
                for item in self.evidence_spans
            ],
        }


__all__ = [
    "ConstraintClaim",
    "EffectClaim",
    "EvidenceSpan",
    "IntentClaimV1",
    "TargetSelectorClaim",
]

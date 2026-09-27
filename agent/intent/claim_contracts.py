"""Pure, authority-neutral contracts for typed intent claims.

This module validates already-decoded claim values and exposes the shared
schema/grammar descriptions.  JSON decoding and semantic interpretation stay
in ``agent.interaction``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .claim_types import (
    _AMBIGUITIES,
    _CONSTRAINT_KINDS,
    _OPERATIONS,
    _POLARITIES,
    _SELECTOR_KINDS,
    _SELECTOR_ROLES,
    INTENT_CLAIM_SCHEMA_VERSION,
    MAX_CONSTRAINT_VALUE_LENGTH,
    MAX_CONSTRAINTS,
    MAX_EFFECT_LENGTH,
    MAX_EFFECTS,
    MAX_EVIDENCE_SPANS,
    MAX_ID_LENGTH,
    MAX_SELECTORS,
    MAX_VALUE_LENGTH,
    ConstraintClaim,
    EffectClaim,
    EvidenceSpan,
    IntentClaimError,
    IntentClaimV1,
    TargetSelectorClaim,
    _list,
)

# The grammar is intentionally a structural transport fallback.  The parser
# above remains authoritative in every mode and rejects all unexpected keys.
INTENT_CLAIM_GBNF = r'''root ::= ws object ws
object ::= "{" ws (members)? ws "}"
members ::= string ws ":" ws value (ws "," ws string ws ":" ws value)*
value ::= string | number | array | object | "true" | "false" | "null"
array ::= "[" ws (value (ws "," ws value)*)? ws "]"
number ::= "0" | [1-9][0-9]*
string ::= "\"" chars "\""
chars ::= char*
char ::= [^"\\\x00-\x1F] | escape
escape ::= "\\" (["\\/bfnrt] | "u" hex hex hex hex)
hex ::= [0-9a-fA-F]
ws ::= [ \t\n\r]*'''


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


def _exact_keys(value: Any, expected: set[str], field: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise IntentClaimError(f"{field} must be an object")
    if set(value) != expected:
        raise IntentClaimError(f"{field} keys are not exact")
    return value


def bind_current_subject_evidence(claim: IntentClaimV1, subject: str) -> IntentClaimV1:
    """Prove every emitted span against the exact current user subject."""

    if not isinstance(claim, IntentClaimV1):
        raise IntentClaimError("claim is not an IntentClaimV1")
    if type(subject) is not str:
        raise IntentClaimError("current subject must be a string")
    for span in claim.evidence_spans:
        if not (0 <= span.start < span.end <= len(subject)):
            raise IntentClaimError("evidence span is outside the current subject")
        if subject[span.start : span.end] != span.text:
            raise IntentClaimError("evidence span does not match the current subject")
    return claim


def evidence_is_current_subject(claim: IntentClaimV1, subject: str) -> bool:
    try:
        bind_current_subject_evidence(claim, subject)
    except IntentClaimError:
        return False
    return True


def _parse_evidence(value: Any) -> Any:
    raw = _exact_keys(value, {"span_id", "start", "end", "text"}, "evidence_span")
    return EvidenceSpan(raw["span_id"], raw["start"], raw["end"], raw["text"])


def _parse_effect(value: Any) -> Any:
    raw = _exact_keys(
        value,
        {"effect", "polarity", "selector_ids", "evidence_span_ids"},
        "effect",
    )
    return EffectClaim(
        raw["effect"],
        raw["polarity"],
        tuple(raw["selector_ids"]),
        tuple(raw["evidence_span_ids"]),
    )


def _parse_selector(value: Any) -> Any:
    raw = _exact_keys(
        value,
        {"selector_id", "kind", "value", "role", "evidence_span_ids"},
        "selector",
    )
    return TargetSelectorClaim(
        raw["selector_id"],
        raw["kind"],
        raw["value"],
        raw["role"],
        tuple(raw["evidence_span_ids"]),
    )


def _parse_constraint(value: Any) -> Any:
    if not isinstance(value, dict):
        raise IntentClaimError("constraint must be an object")
    allowed = {"kind", "value", "selector_ids", "evidence_span_ids"}
    if set(value) - allowed or "kind" not in value or "evidence_span_ids" not in value:
        raise IntentClaimError("constraint keys are not supported")
    return ConstraintClaim(
        value["kind"],
        tuple(value["evidence_span_ids"]),
        value.get("value"),
        tuple(value.get("selector_ids", ())),
    )


def validate_intent_claim(value: Any, *, subject: str | None = None) -> IntentClaimV1:
    """Validate one already-decoded claim object and optionally bind evidence."""

    if not isinstance(value, dict):
        raise IntentClaimError("intent claim must be an object")
    _reject_surrogates(value)
    raw = _exact_keys(
        value,
        {"schema_version", "operation", "ambiguity", "effects", "selectors", "constraints", "evidence_spans"},
        "intent claim",
    )
    if raw["schema_version"] != INTENT_CLAIM_SCHEMA_VERSION:
        raise IntentClaimError("intent claim schema version is invalid")
    effects = tuple(_parse_effect(item) for item in _list(raw["effects"], "effects", MAX_EFFECTS))
    selectors = tuple(_parse_selector(item) for item in _list(raw["selectors"], "selectors", MAX_SELECTORS))
    constraints = tuple(
        _parse_constraint(item)
        for item in _list(raw["constraints"], "constraints", MAX_CONSTRAINTS)
    )
    evidence_spans = tuple(
        _parse_evidence(item)
        for item in _list(raw["evidence_spans"], "evidence_spans", MAX_EVIDENCE_SPANS)
    )
    claim = IntentClaimV1(
        raw["operation"],
        raw["ambiguity"],
        effects,
        selectors,
        constraints,
        evidence_spans,
    )
    if subject is not None:
        bind_current_subject_evidence(claim, subject)
    return claim


INTENT_CLAIM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema_version",
        "operation",
        "ambiguity",
        "effects",
        "selectors",
        "constraints",
        "evidence_spans",
    ],
    "properties": {
        "schema_version": {"type": "string", "const": INTENT_CLAIM_SCHEMA_VERSION},
        "operation": {"type": "string", "enum": sorted(_OPERATIONS)},
        "ambiguity": {"type": "string", "enum": sorted(_AMBIGUITIES)},
        "effects": {
            "type": "array",
            "maxItems": MAX_EFFECTS,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["effect", "polarity", "selector_ids", "evidence_span_ids"],
                "properties": {
                    "effect": {"type": "string", "minLength": 1, "maxLength": MAX_EFFECT_LENGTH},
                    "polarity": {"type": "string", "enum": sorted(_POLARITIES)},
                    "selector_ids": {"type": "array", "maxItems": MAX_SELECTORS, "items": {"type": "string"}},
                    "evidence_span_ids": {"type": "array", "maxItems": MAX_EVIDENCE_SPANS, "items": {"type": "string"}},
                },
            },
        },
        "selectors": {
            "type": "array",
            "maxItems": MAX_SELECTORS,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["selector_id", "kind", "value", "role", "evidence_span_ids"],
                "properties": {
                    "selector_id": {"type": "string", "minLength": 1, "maxLength": MAX_ID_LENGTH},
                    "kind": {"type": "string", "enum": sorted(_SELECTOR_KINDS)},
                    "value": {"type": "string", "minLength": 1, "maxLength": MAX_VALUE_LENGTH},
                    "role": {"type": "string", "enum": sorted(_SELECTOR_ROLES)},
                    "evidence_span_ids": {"type": "array", "maxItems": MAX_EVIDENCE_SPANS, "items": {"type": "string"}},
                },
            },
        },
        "constraints": {
            "type": "array",
            "maxItems": MAX_CONSTRAINTS,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["kind", "evidence_span_ids"],
                "properties": {
                    "kind": {"type": "string", "enum": sorted(_CONSTRAINT_KINDS)},
                    "value": {"type": "string", "maxLength": MAX_CONSTRAINT_VALUE_LENGTH},
                    "selector_ids": {"type": "array", "maxItems": MAX_SELECTORS, "items": {"type": "string"}},
                    "evidence_span_ids": {"type": "array", "maxItems": MAX_EVIDENCE_SPANS, "items": {"type": "string"}},
                },
            },
        },
        "evidence_spans": {
            "type": "array",
            "maxItems": MAX_EVIDENCE_SPANS,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["span_id", "start", "end", "text"],
                "properties": {
                    "span_id": {"type": "string", "minLength": 1, "maxLength": MAX_ID_LENGTH},
                    "start": {"type": "integer", "minimum": 0},
                    "end": {"type": "integer", "minimum": 1},
                    "text": {"type": "string", "minLength": 1, "maxLength": MAX_VALUE_LENGTH},
                },
            },
        },
    },
}
__all__ = [
    "INTENT_CLAIM_GBNF", "INTENT_CLAIM_SCHEMA", "INTENT_CLAIM_SCHEMA_VERSION",
    "IntentClaimError", "MAX_CONSTRAINT_VALUE_LENGTH", "MAX_CONSTRAINTS",
    "MAX_EFFECT_LENGTH", "MAX_EFFECTS", "MAX_EVIDENCE_SPANS", "MAX_ID_LENGTH",
    "MAX_SELECTORS", "MAX_VALUE_LENGTH", "bind_current_subject_evidence",
    "evidence_is_current_subject", "validate_intent_claim",
]

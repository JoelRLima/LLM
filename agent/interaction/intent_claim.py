"""Strict, explicitly untrusted semantic intent claims.

The semantic resolver owns interpretation, but this module owns only the
typed wire boundary.  Nothing in this module grants capabilities, resolves a
path, or approves an operation.  Those facts are derived later by the
deterministic admission owners.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from .intent_claim_schema import INTENT_CLAIM_GBNF, INTENT_CLAIM_SCHEMA
from .intent_claim_types import (
    ConstraintClaim,
    EffectClaim,
    EvidenceSpan,
    IntentClaimV1,
    TargetSelectorClaim,
)
from .intent_claim_validation import (
    INTENT_CLAIM_SCHEMA_VERSION,
    MAX_CONSTRAINTS,
    MAX_EFFECTS,
    MAX_EVIDENCE_SPANS,
    MAX_SELECTORS,
    IntentClaimError,
    _exact_keys,
    _list,
    _reject_constant,
    _reject_duplicate_keys,
    _reject_surrogates,
)


def _parse_evidence(value: Any) -> EvidenceSpan:
    raw = _exact_keys(value, {"span_id", "start", "end", "text"}, "evidence_span")
    return EvidenceSpan(raw["span_id"], raw["start"], raw["end"], raw["text"])


def _parse_effect(value: Any) -> EffectClaim:
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


def _parse_selector(value: Any) -> TargetSelectorClaim:
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


def _parse_constraint(value: Any) -> ConstraintClaim:
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
    """Validate one decoded object, optionally binding it to the exact subject."""

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


def parse_intent_claim(raw: str | Mapping[str, Any], *, subject: str | None = None) -> IntentClaimV1:
    """Parse strict JSON or validate a decoded claim without repair/salvage."""

    value: Any
    if isinstance(raw, str):
        try:
            value = json.loads(
                raw.strip(),
                object_pairs_hook=_reject_duplicate_keys,
                parse_constant=_reject_constant,
            )
        except IntentClaimError:
            raise
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise IntentClaimError("intent claim is not strict JSON") from exc
    elif isinstance(raw, Mapping):
        value = dict(raw)
    else:
        raise IntentClaimError("intent claim input must be JSON text or object")
    return validate_intent_claim(value, subject=subject)


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


__all__ = [
    "ConstraintClaim",
    "EffectClaim",
    "EvidenceSpan",
    "INTENT_CLAIM_GBNF",
    "INTENT_CLAIM_SCHEMA",
    "INTENT_CLAIM_SCHEMA_VERSION",
    "IntentClaimError",
    "IntentClaimV1",
    "TargetSelectorClaim",
    "bind_current_subject_evidence",
    "evidence_is_current_subject",
    "parse_intent_claim",
    "validate_intent_claim",
]

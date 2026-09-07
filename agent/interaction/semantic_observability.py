"""Bounded, non-authoritative semantic parser evidence."""

from __future__ import annotations

import re
from typing import Any

from agent.planning.intent_continuation import claim_fingerprint


def emit_semantic_audit(application: Any, event_type: str, data: dict[str, Any]) -> None:
    emit = getattr(application, "_emit", None)
    if callable(emit):
        try:
            emit(event_type, data)
        except (RuntimeError, TypeError, ValueError):
            return


def semantic_parse_failure_evidence(reason: str) -> dict[str, Any]:
    return {
        "contract": "semantic-intent-v1",
        "success": False,
        "failure_reason": reason,
        "evidence_span_ids": [],
    }


def semantic_parse_success_evidence(claim: Any) -> dict[str, Any]:
    claim_hash = None
    try:
        claim_hash = claim_fingerprint(claim)
    except (TypeError, ValueError):
        pass
    return {
        "contract": "semantic-intent-v1",
        "success": True,
        "evidence_span_ids": [
            item.span_id for item in getattr(claim, "evidence_spans", ())
        ],
        "claim_fingerprint": claim_hash,
    }


def semantic_admission_denied_evidence(
    claim: Any,
    reason_code: str,
) -> dict[str, Any]:
    raw_reason = str(reason_code).strip().upper()
    reason = raw_reason if re.fullmatch(r"[A-Z0-9_]{1,64}", raw_reason) else "INTENT_AUTHORITY_DENIED"
    return {
        "contract": "semantic-intent-v1",
        "admitted": False,
        "reason_code": reason,
        "evidence_span_ids": [
            item.span_id for item in getattr(claim, "evidence_spans", ())
        ],
        "claim_fingerprint": semantic_parse_success_evidence(claim).get(
            "claim_fingerprint"
        ),
    }


def emit_semantic_parse_event(
    application: Any,
    claim: Any | None = None,
    *,
    failure_reason: str | None = None,
) -> None:
    if claim is None:
        if failure_reason is None:
            return
        evidence = semantic_parse_failure_evidence(failure_reason)
    else:
        evidence = semantic_parse_success_evidence(claim)
    emit_semantic_audit(application, "semantic_intent_parsed", evidence)


def emit_semantic_admission_denied_event(
    application: Any,
    claim: Any,
    reason_code: str,
) -> None:
    emit_semantic_audit(
        application,
        "semantic_intent_admission_denied",
        semantic_admission_denied_evidence(claim, reason_code),
    )


def emit_semantic_admission_denied_if_claim(
    application: Any,
    claim: Any | None,
    reason_code: str,
) -> None:
    if claim is not None:
        emit_semantic_admission_denied_event(application, claim, reason_code)


def emit_semantic_parse_if_needed(
    application: Any,
    claim: Any | None,
    already_emitted: bool,
) -> None:
    if claim is not None and not already_emitted:
        emit_semantic_parse_event(application, claim)


__all__ = [
    "emit_semantic_audit",
    "emit_semantic_admission_denied_event",
    "emit_semantic_admission_denied_if_claim",
    "emit_semantic_parse_event",
    "emit_semantic_parse_if_needed",
    "semantic_admission_denied_evidence",
    "semantic_parse_failure_evidence",
    "semantic_parse_success_evidence",
]

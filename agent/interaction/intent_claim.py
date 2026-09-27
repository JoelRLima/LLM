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

from agent.intent.claim_contracts import (
    INTENT_CLAIM_GBNF,
    INTENT_CLAIM_SCHEMA,
    INTENT_CLAIM_SCHEMA_VERSION,
    IntentClaimError,
    _reject_constant,
    _reject_duplicate_keys,
    bind_current_subject_evidence,
    evidence_is_current_subject,
    validate_intent_claim,
)
from agent.intent.claim_types import (
    ConstraintClaim,
    EffectClaim,
    EvidenceSpan,
    IntentClaimV1,
    TargetSelectorClaim,
)


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

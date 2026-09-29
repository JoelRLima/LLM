"""Compatibility exports for the canonical intent claim contracts."""

from llm_agent.agent.intent.claim_contracts import (
    INTENT_CLAIM_SCHEMA_VERSION,
    MAX_CONSTRAINT_VALUE_LENGTH,
    MAX_CONSTRAINTS,
    MAX_EFFECT_LENGTH,
    MAX_EFFECTS,
    MAX_EVIDENCE_SPANS,
    MAX_ID_LENGTH,
    MAX_SELECTORS,
    MAX_VALUE_LENGTH,
    IntentClaimError,
)

__all__ = [
    "INTENT_CLAIM_SCHEMA_VERSION",
    "MAX_CONSTRAINT_VALUE_LENGTH",
    "MAX_CONSTRAINTS",
    "MAX_EFFECT_LENGTH",
    "MAX_EFFECTS",
    "MAX_EVIDENCE_SPANS",
    "MAX_ID_LENGTH",
    "MAX_SELECTORS",
    "MAX_VALUE_LENGTH",
    "IntentClaimError",
]

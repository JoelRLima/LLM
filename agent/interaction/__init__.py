"""Unified interaction admission boundary."""

from .errors import public_explanation
from .intent_claim import (
    ConstraintClaim,
    EffectClaim,
    EvidenceSpan,
    IntentClaimError,
    IntentClaimV1,
    TargetSelectorClaim,
    bind_current_subject_evidence,
    parse_intent_claim,
)
from .service import InteractionService
from .types import (
    ActionGrounding,
    AgentInteractionResult,
    InteractionAction,
    InteractionAmbiguity,
    InteractionBoundary,
    InteractionModelDecision,
    InteractionProvenance,
    InteractionResolution,
)

__all__ = [
    "ActionGrounding",
    "AgentInteractionResult",
    "InteractionAction",
    "InteractionAmbiguity",
    "InteractionBoundary",
    "InteractionModelDecision",
    "InteractionProvenance",
    "InteractionResolution",
    "InteractionService",
    "ConstraintClaim",
    "EffectClaim",
    "EvidenceSpan",
    "IntentClaimError",
    "IntentClaimV1",
    "TargetSelectorClaim",
    "bind_current_subject_evidence",
    "parse_intent_claim",
    "public_explanation",
]

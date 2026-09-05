"""Unified interaction admission boundary."""

from .errors import public_explanation
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
    "public_explanation",
]

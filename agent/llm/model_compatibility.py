"""Typed, profile-owned compatibility policy for model request geometry."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class StructuredReasoningPolicy(str, Enum):
    ALLOW = "allow"
    DISABLE_REASONING = "disable_reasoning"


@dataclass(frozen=True, slots=True)
class ModelCompatibility:
    """Compatibility policy declared by one resolved model profile."""

    structured_reasoning: StructuredReasoningPolicy = StructuredReasoningPolicy.ALLOW

    def to_dict(self) -> dict[str, str]:
        """Return the stable, deliberately narrow serialized projection."""

        return {"structured_reasoning": self.structured_reasoning.value}


__all__ = ["ModelCompatibility", "StructuredReasoningPolicy"]

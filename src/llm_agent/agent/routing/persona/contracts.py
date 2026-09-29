"""Typed persona-routing request, decision and router port."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol


class PersonaId(str, Enum):
    CODER = "coder"
    RESEARCHER = "researcher"
    GENERAL = "general"
    SECURITY_AUDITOR = "security_auditor"


class PersonaRouteSource(str, Enum):
    HEURISTIC_TRIVIAL = "heuristic_trivial"
    HEURISTIC_SECURITY = "heuristic_security"
    HEURISTIC_LISTING = "heuristic_listing"
    MODEL = "model"
    MODEL_INVALID_FALLBACK = "model_invalid_fallback"


@dataclass(frozen=True, slots=True)
class PersonaRouteRequest:
    objective: str

    def __post_init__(self) -> None:
        if not isinstance(self.objective, str) or not self.objective.strip():
            raise ValueError("objective must be a non-empty string")
        if len(self.objective) > 8_192:
            raise ValueError("objective exceeds 8192 characters")


@dataclass(frozen=True, slots=True)
class PersonaRouteDecision:
    persona: PersonaId
    source: PersonaRouteSource
    reason_code: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.persona, PersonaId):
            raise TypeError("persona must be PersonaId")
        if not isinstance(self.source, PersonaRouteSource):
            raise TypeError("source must be PersonaRouteSource")


class PersonaRouter(Protocol):
    def route(self, request: PersonaRouteRequest) -> PersonaRouteDecision: ...


__all__ = [
    "PersonaId",
    "PersonaRouteDecision",
    "PersonaRouteRequest",
    "PersonaRouteSource",
    "PersonaRouter",
]

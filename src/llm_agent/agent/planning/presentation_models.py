"""Bounded value objects shared by canonical planning presentation renderers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class PlanningPresentationError(ValueError):
    """Raised when a planner catalog cannot be represented safely."""


@dataclass(frozen=True, slots=True)
class PlanningPresentationBudget:
    """Deterministic catalog limits sized against the existing context budget."""

    max_tools: int = 64
    max_name_chars: int = 128
    max_description_chars: int = 2_000
    max_schema_chars: int = 4_096
    max_tool_chars: int = 8_192
    max_catalog_chars: int = 16_384

    @classmethod
    def for_context_limit(cls, context_limit: int) -> "PlanningPresentationBudget":
        total = max(8_192, min(16_384, int(context_limit) * 2))
        return cls(max_catalog_chars=total)


@dataclass(frozen=True, slots=True)
class PlanningToolIndexEntry:
    """The bounded Level-0 projection of one canonical planning tool."""

    name: str
    purpose: str
    category: str
    reads_workspace: bool
    mutation: bool
    required_capabilities: tuple[str, ...] = ()

    def as_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "name": self.name,
            "purpose": self.purpose,
            "category": self.category,
            "read": self.reads_workspace,
            "mutation": self.mutation,
        }
        if self.required_capabilities:
            payload["required_capabilities"] = list(self.required_capabilities)
        return payload


__all__ = [
    "PlanningPresentationBudget",
    "PlanningPresentationError",
    "PlanningToolIndexEntry",
]

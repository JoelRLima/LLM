"""Typed control-flow results for optional orchestration routes."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class RouteDisposition(str, Enum):
    NOT_APPLICABLE = "not_applicable"
    FALLBACK = "fallback"
    HANDLED = "handled"


@dataclass(frozen=True, slots=True)
class RouteResult:
    """Control-flow evidence; never an independent task-status authority."""

    route: str
    disposition: RouteDisposition
    reason_code: str | None = None
    detail: str | None = None
    answer: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.route, str) or not self.route.strip():
            raise ValueError("route must be a non-empty string")
        if not isinstance(self.disposition, RouteDisposition):
            object.__setattr__(self, "disposition", RouteDisposition(str(self.disposition)))

    @classmethod
    def not_applicable(cls, route: str, *, reason_code: str | None = None) -> "RouteResult":
        return cls(route, RouteDisposition.NOT_APPLICABLE, reason_code=reason_code)

    @classmethod
    def fallback(
        cls,
        route: str,
        *,
        reason_code: str,
        detail: str | None = None,
    ) -> "RouteResult":
        return cls(route, RouteDisposition.FALLBACK, reason_code=reason_code, detail=detail)

    @classmethod
    def handled(
        cls,
        route: str,
        *,
        answer: str | None = None,
        reason_code: str | None = None,
        detail: str | None = None,
    ) -> "RouteResult":
        return cls(
            route,
            RouteDisposition.HANDLED,
            reason_code=reason_code,
            detail=detail,
            answer=answer,
        )


__all__ = ["RouteDisposition", "RouteResult"]

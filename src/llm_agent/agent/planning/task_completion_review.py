"""Typed result of the read-only task completion review."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from llm_agent.agent.planning.task_semantics import TaskObligation


@dataclass(frozen=True, slots=True)
class CompletionReview:
    """Read-only decision produced by the canonical completion owner."""

    accepted: bool
    reason_code: str | None = None
    existing_disposition: str | None = None
    pending_effects: tuple[str, ...] = ()
    pending_obligations: tuple[TaskObligation, ...] = ()
    blocked_obligations: tuple[TaskObligation, ...] = ()
    prohibited_effects: tuple[str, ...] = ()
    unrequested_effects: tuple[str, ...] = ()
    unrecovered_failure: bool = False
    requirement_verification: dict[str, Any] | None = None


__all__ = ["CompletionReview"]

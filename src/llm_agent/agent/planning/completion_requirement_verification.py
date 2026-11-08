"""Completion gate for admitted structured requirement checks."""

from __future__ import annotations

from typing import Any


def review_requirement_verification(orchestrator: Any) -> tuple[dict[str, Any], str | None]:
    """Return the fresh check document and its completion blocker, if any."""
    from llm_agent.agent.planning.outcome_verification import (
        requirement_verification_document,
    )

    document = requirement_verification_document(orchestrator)
    if not document or document.get("overall_status") == "satisfied":
        return document, None
    reason = (
        "REQUIREMENT_VERIFICATION_FAILED"
        if document.get("overall_status") == "violated"
        else "REQUIREMENT_VERIFICATION_INCONCLUSIVE"
    )
    return document, reason


__all__ = ["review_requirement_verification"]

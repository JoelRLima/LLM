"""Fail-closed vocabulary for interactive approval prompts."""

from __future__ import annotations

from llm_agent.application.approval import ApprovalResponse


def parse_console_approval(value: str) -> ApprovalResponse:
    normalized = value.strip().casefold()
    if normalized in {"s", "sim", "y", "yes"}:
        return ApprovalResponse.APPROVE
    return ApprovalResponse.DENY


__all__ = ["parse_console_approval"]

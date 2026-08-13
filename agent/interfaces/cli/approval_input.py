"""Fail-closed vocabulary for interactive approval prompts."""

from __future__ import annotations

from agent.approval import ApprovalDecision


def parse_console_approval(value: str) -> ApprovalDecision:
    normalized = value.strip().casefold()
    if normalized in {"s", "sim", "y", "yes"}:
        return ApprovalDecision.APPROVED
    return ApprovalDecision.REJECTED


__all__ = ["parse_console_approval"]

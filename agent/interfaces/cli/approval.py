"""Interactive implementation of the approval port."""

from __future__ import annotations

from agent.approval import ApprovalDecision, ApprovalRequest
from agent.interfaces.cli.ui import console


class ConsoleApproval:
    def request(self, request: ApprovalRequest) -> ApprovalDecision:
        answer = console.input(f"[bold cyan]{request.prompt} [s/N]:[/bold cyan] ")
        if answer.strip().casefold() in {"s", "sim"}:
            return ApprovalDecision.APPROVED
        return ApprovalDecision.REJECTED


__all__ = ["ConsoleApproval"]

"""Interactive implementation of the approval port."""

from __future__ import annotations

from llm_agent.application.agent_boundary import ApprovalDecision, ApprovalRequest
from llm_agent.interfaces.cli.approval_input import parse_console_approval
from llm_agent.interfaces.cli.ui import console


class ConsoleApproval:
    def request(self, request: ApprovalRequest) -> ApprovalDecision:
        answer = console.input(
            f"[bold cyan]{request.prompt}[/bold cyan]\n"
            "[dim]s/sim/y/yes = aprovar · Enter/n/nao/não/no = negar:[/dim] "
        )
        return parse_console_approval(answer)


__all__ = ["ConsoleApproval", "parse_console_approval"]

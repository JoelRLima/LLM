from threading import Lock
from typing import Any

from rich.console import Console
from rich.syntax import Syntax
from rich.table import Table

from agent.approval import ApprovalDecision
from agent.code.changes import ChangePreview
from agent.code.policy import ProposalAssessment
from agent.interfaces.cli.approval_input import parse_console_approval
from agent.interfaces.cli.interactive_shell import prompt_from
from agent.runtime.context import TaskResult

console = Console()


class ConsoleChangeApprover:
    # CLI edits always carry an explicit human approval authority.  This is
    # intentionally independent of the confidence threshold so an unavailable
    # validator cannot make an unattended write durable.
    requires_explicit_approval = True

    def __init__(self, assume_yes: bool = False, *, prompt: Any | None = None) -> None:
        self.assume_yes = assume_yes
        self.prompt = prompt
        self._lock = Lock()

    def approve(self, preview: ChangePreview, assessment: ProposalAssessment) -> bool:
        if self.assume_yes:
            return True
        with self._lock:
            console.print(f"[bold yellow]Proposta exige confirmação (confiança {assessment.confidence:.0%}).[/bold yellow]")
            for reason in assessment.reasons:
                console.print(f"- {reason}", markup=False)
            console.print(Syntax(preview.diff or "(diff vazio)", "diff", word_wrap=True))
            message = "[bold cyan]Aplicar este ChangeSet? [s/sim/y/yes = sim; Enter/n/nao/não/no = não]:[/bold cyan] "
            answer = self.prompt(message) if self.prompt is not None else prompt_from(console, message)
            return parse_console_approval(str(answer or "")) is ApprovalDecision.APPROVED


def render_code_result(result: TaskResult) -> None:
    summary = result.summary or result.error or result.status.value
    console.print(f"{result.status.value.upper()}: {summary}", markup=False)
    for artifact in result.artifacts:
        if artifact.content:
            lexer = "diff" if artifact.kind == "changeset" else "json"
            content = artifact.content[:20_000]
            if len(artifact.content) > len(content):
                content += "\n... saída truncada pela CLI ..."
            console.print(Syntax(content, lexer, word_wrap=True))
    for diagnostic in result.diagnostics:
        console.print(
            f"{diagnostic.get('code', 'diagnostic')} "
            f"{diagnostic.get('file_path', '')}:{diagnostic.get('line', '')} "
            f"{diagnostic.get('message', '')}",
            markup=False,
        )


def exibir_menu() -> None:
    from agent.actions import DEFAULT_ACTION_CATALOG
    from agent.interfaces.cli.action_registry import DEFAULT_CLI_ACTION_REGISTRY

    table = Table(title="Comandos Disponíveis", show_header=True, header_style="bold magenta")
    table.add_column("Comando", style="cyan", width=20)
    table.add_column("Descrição")
    definitions = {item.action_id: item for item in DEFAULT_ACTION_CATALOG}
    for binding in DEFAULT_CLI_ACTION_REGISTRY._bindings:
        if binding.advertised:
            definition = definitions[binding.action_id]
            preferred = "/" + " ".join(binding.preferred_path).lstrip("/")
            aliases = tuple(" ".join(alias) for alias in binding.aliases)
            label = preferred if not aliases else f"{preferred} (aliases: {', '.join(aliases)})"
            table.add_row(label, definition.description)
    console.print(table)

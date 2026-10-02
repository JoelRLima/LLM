from threading import Lock
from typing import Any

from rich.console import Console
from rich.syntax import Syntax
from rich.table import Table

from llm_agent.application.agent_boundary import ApprovalDecision
from llm_agent.application.code_commands import CodeCommandOutcome
from llm_agent.application.code_review import CodeReviewAssessment, CodeReviewPreview
from llm_agent.interfaces.cli.approval_input import parse_console_approval
from llm_agent.interfaces.cli.interactive_shell import prompt_from

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

    def approve(self, preview: CodeReviewPreview, assessment: CodeReviewAssessment) -> bool:
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


def render_code_result(result: CodeCommandOutcome) -> None:
    summary = result.summary or result.error or result.status
    console.print(f"{result.status.upper()}: {summary}", markup=False)
    for kind, artifact_content in result.artifacts:
        if artifact_content:
            lexer = "diff" if kind == "changeset" else "json"
            content = artifact_content[:20_000]
            if len(artifact_content) > len(content):
                content += "\n... sa\u00edda truncada pela CLI ..."
            console.print(Syntax(content, lexer, word_wrap=True))
    for code, file_path, line, message in result.diagnostics:
        console.print(f"{code} {file_path}:{line} {message}", markup=False)


def exibir_menu() -> None:
    from llm_agent.actions import DEFAULT_ACTION_CATALOG
    from llm_agent.interfaces.cli.action_registry import DEFAULT_CLI_ACTION_REGISTRY

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

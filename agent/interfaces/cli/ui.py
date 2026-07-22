from threading import Lock

from rich.console import Console
from rich.syntax import Syntax
from rich.table import Table

from agent.code.changes import ChangePreview
from agent.code.policy import ProposalAssessment
from agent.runtime.context import TaskResult

console = Console()


class ConsoleChangeApprover:
    def __init__(self, assume_yes: bool = False) -> None:
        self.assume_yes = assume_yes
        self._lock = Lock()

    def approve(self, preview: ChangePreview, assessment: ProposalAssessment) -> bool:
        if self.assume_yes:
            return True
        with self._lock:
            console.print(f"[bold yellow]Proposta exige confirmação (confiança {assessment.confidence:.0%}).[/bold yellow]")
            for reason in assessment.reasons:
                console.print(f"  [yellow]- {reason}[/yellow]")
            console.print(Syntax(preview.diff or "(diff vazio)", "diff", word_wrap=True))
            answer = console.input("[bold cyan]Aplicar este ChangeSet? [s/N]:[/bold cyan] ")
            return answer.strip().casefold() in {"s", "sim", "y", "yes"}


def render_code_result(result: TaskResult) -> None:
    colors = {
        "succeeded": "green", "unverified": "yellow", "blocked": "yellow",
        "cancelled": "yellow", "failed": "red",
    }
    color = colors.get(result.status.value, "white")
    summary = result.summary or result.error or result.status.value
    console.print(f"[bold {color}]{result.status.value.upper()}:[/bold {color}] {summary}")
    for artifact in result.artifacts:
        if artifact.content:
            lexer = "diff" if artifact.kind == "changeset" else "json"
            content = artifact.content[:20_000]
            if len(artifact.content) > len(content):
                content += "\n... saída truncada pela CLI ..."
            console.print(Syntax(content, lexer, word_wrap=True))
    for diagnostic in result.diagnostics:
        console.print(
            f"[dim]{diagnostic.get('code', 'diagnostic')} "
            f"{diagnostic.get('file_path', '')}:{diagnostic.get('line', '')}[/dim] "
            f"{diagnostic.get('message', '')}"
        )


def exibir_menu() -> None:
    table = Table(title="Comandos Disponíveis", show_header=True, header_style="bold magenta")
    table.add_column("Comando", style="cyan", width=20)
    table.add_column("Descrição")
    rows = (
        ("/system, /sistema", "Altera as regras em tempo real"),
        ("/prompt", "Mostra o System Prompt ativo"),
        ("/think, /pensar", "Liga/desliga o pensamento"),
        ("/clear, /limpar", "Limpa o histórico de conversas"),
        ("/save, /salvar", "Salva o histórico em um arquivo"),
        ("/load, /carregar", "Carrega o histórico de um arquivo"),
        ("/agent, /agente", "Ativa/desativa ou executa o modo agente"),
        ("/code help", "Workflows explícitos de código sem planner"),
        ("/debug", "Alterna modo diagnóstico"),
        ("/memory, /memoria", "Mostra a memória do agente"),
        ("/events", "Mostra os eventos da última execução"),
        ("/doctor, /diagnostico", "Executa o diagnóstico de saúde"),
        ("/retry, /retomar", "Retoma a tarefa interrompida"),
        ("/ls, /list", "Lista os arquivos do projeto"),
        ("/read <arquivo>", "Lê um arquivo"),
        ("/find <texto>", "Busca texto nos arquivos"),
        ("/search <consulta>", "Pesquisa na web"),
        ("/help, /ajuda", "Mostra esta ajuda"),
        ("exit, sair", "Encerra o programa"),
    )
    for command, description in rows:
        table.add_row(command, description)
    console.print(table)

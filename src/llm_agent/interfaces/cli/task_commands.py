"""CLI handlers that submit and resume Agent tasks."""

from __future__ import annotations

from typing import Any

from rich.panel import Panel

from llm_agent.application.task_directives import TaskDirectiveParseError
from llm_agent.application.task_execution import execute_submission, observe_task_settlement


def agent_command(text: str, ctx: Any, *, output: Any) -> None:
    parts = text.strip().split(maxsplit=1)
    if len(parts) == 1:
        output.print("Modo agente: unificado. Use /agent <objetivo> para abrir uma tarefa ou /agent /continue para retomar a tarefa anterior.")
        return
    try:
        settlement = execute_submission(ctx.task_execution, parts[1], entry="agent", visible_text=text)
        result = observe_task_settlement(settlement, channel="interactive")
        answer = str(result.get("answer", ""))
        output.print(Panel(answer, title="[bold blue]Agente[/bold blue]"))
        if result["legacy_transcript"]:
            from llm_agent.interfaces.cli.legacy_compat import append_legacy_answer
            append_legacy_answer(ctx, answer)
    except TaskDirectiveParseError as exc:
        output.print(f"[bold red]Erro [{exc.reason_code}]: {exc.detail}[/bold red]")
    except KeyboardInterrupt:
        output.print("\n[bold red]Agente interrompido.[/bold red]")


def retry(text: str, ctx: Any, *, output: Any) -> None:
    output.print("[bold yellow]Verificando checkpoint...[/bold yellow]")
    settlement = execute_submission(ctx.task_execution, "/continue", entry="retry", visible_text=text or "/retry")
    result = observe_task_settlement(settlement, channel="interactive")
    answer = result["answer"]
    output.print(Panel(answer, title="[bold blue]Agente[/bold blue]"))
    if result["legacy_transcript"]:
        from llm_agent.interfaces.cli.legacy_compat import append_legacy_answer
        append_legacy_answer(ctx, answer)


__all__ = ["agent_command", "retry"]

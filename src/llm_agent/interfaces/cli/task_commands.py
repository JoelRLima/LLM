"""CLI handlers that submit and resume Agent tasks."""

from __future__ import annotations

from typing import Any

from rich.panel import Panel

from llm_agent.application.task_directives import TaskDirectiveParseError, parse_task_request


def agent_command(text: str, ctx: Any, *, output: Any) -> None:
    parts = text.strip().split(maxsplit=1)
    if len(parts) == 1:
        output.print(
            "Modo agente: unificado. Use /agent <objetivo> para abrir uma tarefa "
            "ou /agent /continue para retomar a tarefa anterior."
        )
        return
    interact = getattr(ctx.application, "interact", None)
    if callable(interact):
        try:
            result = interact(
                parts[1],
                boundary="task",
                visible_user_text=text,
                task_payload=parts[1],
            )
            answer = str(getattr(result, "answer", ""))
            output.print(Panel(answer, title="[bold blue]Agente[/bold blue]"))
        except KeyboardInterrupt:
            output.print("\n[bold red]Agente interrompido.[/bold red]")
        return
    try:
        request = parse_task_request(parts[1])
    except TaskDirectiveParseError as exc:
        output.print(f"[bold red]Erro [{exc.reason_code}]: {exc.detail}[/bold red]")
        return
    try:
        from llm_agent.interfaces.cli.legacy_compat import dispatch_task_facade

        result = dispatch_task_facade(ctx, request)
        answer = getattr(result, "answer", result)
        answer_text = str(answer)
        output.print(Panel(answer_text, title="[bold blue]Agente[/bold blue]"))
        from llm_agent.interfaces.cli.legacy_compat import append_legacy_answer

        append_legacy_answer(ctx, answer_text)
    except KeyboardInterrupt:
        output.print("\n[bold red]Agente interrompido.[/bold red]")


def retry(text: str, ctx: Any, *, output: Any) -> None:
    output.print("[bold yellow]Verificando checkpoint...[/bold yellow]")
    interact = getattr(ctx.application, "interact", None)
    if callable(interact):
        result = interact(
            "/continue",
            boundary="task",
            visible_user_text=text or "/retry",
            task_payload="/continue",
        )
    else:
        from llm_agent.interfaces.cli.legacy_compat import dispatch_task_facade

        result = dispatch_task_facade(ctx, parse_task_request("/continue"))
    answer = result.answer
    output.print(Panel(answer, title="[bold blue]Agente[/bold blue]"))
    if not callable(interact):
        from llm_agent.interfaces.cli.legacy_compat import append_legacy_answer

        append_legacy_answer(ctx, answer)


__all__ = ["agent_command", "retry"]

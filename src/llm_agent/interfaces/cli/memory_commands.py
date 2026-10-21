"""Interactive session-memory command handlers."""

from __future__ import annotations

from typing import Any

from rich.table import Table

from llm_agent.application.task_execution import execute_memory_command
from llm_agent.interfaces.cli.interactive_input import prompt_value as _prompt_value
from llm_agent.interfaces.cli.ui import console
from llm_agent.interfaces.cli.workspace_entry import workspace_storage_path


def remember(text: str, ctx: Any) -> None:
    parts = text.strip().split(maxsplit=3)
    offset = 2 if len(parts) > 1 and parts[1].casefold() == "remember" else 1
    if len(parts) <= offset + 1:
        console.print("[bold red]Uso: /remember chave valor[/bold red]")
        return
    key, value = parts[offset], parts[offset + 1]
    execute_memory_command(ctx.task_execution, "remember", key=key, value=value)
    console.print(f"[bold green]Lembrei:[/bold green] {key} = {value}")


def show_memory(_: str, ctx: Any) -> None:
    table = Table(title="Memória da Sessão")
    table.add_column("Seção", style="cyan")
    table.add_column("Conteúdo")
    for section, content in execute_memory_command(ctx.task_execution, "show")["rows"]:
        if content:
            table.add_row(section, str(content))
    console.print(table)


def forget(_: str, ctx: Any) -> None:
    key = _prompt_value(ctx, "[bold cyan]Chave a esquecer:[/bold cyan] ")
    execute_memory_command(ctx.task_execution, "forget", key=key)
    console.print(f"[bold green]Chave '{key}' removida (se existia).[/bold green]")


def clear_memory(_: str, ctx: Any) -> None:
    execute_memory_command(ctx.task_execution, "clear")
    console.print("[bold green]Memória da sessão limpa.[/bold green]")


def _memory_path(ctx: Any) -> str:
    default = workspace_storage_path(ctx, "memory_file", "agent_memory.json")
    entered = _prompt_value(ctx, f"[bold cyan]Caminho (Enter para '{default}'):[/bold cyan] ", default=str(default))
    return str(entered or default)


def save_memory(_: str, ctx: Any) -> None:
    message = execute_memory_command(ctx.task_execution, "save", path=_memory_path(ctx))["message"]
    console.print(f"[bold green]{message}[/bold green]")


def load_memory(_: str, ctx: Any) -> None:
    message = execute_memory_command(ctx.task_execution, "load", path=_memory_path(ctx))["message"]
    console.print(f"[bold green]{message}[/bold green]")


__all__ = [
    "clear_memory",
    "forget",
    "load_memory",
    "remember",
    "save_memory",
    "show_memory",
]

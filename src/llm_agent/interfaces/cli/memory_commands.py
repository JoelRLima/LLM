"""Interactive session-memory command handlers."""

from __future__ import annotations

from typing import Any

from rich.table import Table

from llm_agent.interactive.action_parser import parse_action
from llm_agent.interactive.local_commands import local_service_for_context
from llm_agent.interfaces.cli.interactive_input import prompt_value as _prompt_value
from llm_agent.interfaces.cli.ui import console
from llm_agent.interfaces.cli.workspace_entry import workspace_storage_path


def remember(text: str, ctx: Any) -> None:
    match = parse_action(text)
    parts = match.raw_payload.split(maxsplit=1) if match is not None else []
    if len(parts) != 2:
        console.print("[bold red]Uso: /remember chave valor[/bold red]")
        return
    key, value = parts
    local_service_for_context(ctx).memory("remember", key=key, value=value)
    console.print(f"[bold green]Lembrei:[/bold green] {key} = {value}")


def show_memory(_: str, ctx: Any) -> None:
    table = Table(title="Memória da Sessão")
    table.add_column("Seção", style="cyan")
    table.add_column("Conteúdo")
    for section, content in local_service_for_context(ctx).memory("show")["rows"]:
        if content:
            table.add_row(section, str(content))
    console.print(table)


def forget(_: str, ctx: Any) -> None:
    key = _prompt_value(ctx, "[bold cyan]Chave a esquecer:[/bold cyan] ")
    if key is None:
        return
    local_service_for_context(ctx).memory("forget", key=key)
    console.print(f"[bold green]Chave '{key}' removida (se existia).[/bold green]")


def clear_memory(_: str, ctx: Any) -> None:
    local_service_for_context(ctx).memory("clear")
    console.print("[bold green]Memória da sessão limpa.[/bold green]")


def _memory_path(ctx: Any) -> str | None:
    default = workspace_storage_path(ctx, "memory_file", "agent_memory.json")
    entered = _prompt_value(ctx, f"[bold cyan]Caminho (Enter para '{default}'):[/bold cyan] ", default=str(default))
    return None if entered is None else str(entered or default)


def save_memory(_: str, ctx: Any) -> None:
    path = _memory_path(ctx)
    if path is None:
        return
    message = local_service_for_context(ctx).memory("save", path=path)["message"]
    console.print(f"[bold green]{message}[/bold green]")


def load_memory(_: str, ctx: Any) -> None:
    path = _memory_path(ctx)
    if path is None:
        return
    message = local_service_for_context(ctx).memory("load", path=path)["message"]
    console.print(f"[bold green]{message}[/bold green]")


__all__ = [
    "clear_memory",
    "forget",
    "load_memory",
    "remember",
    "save_memory",
    "show_memory",
]

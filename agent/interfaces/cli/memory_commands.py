"""Interactive session-memory command handlers."""

from __future__ import annotations

from typing import Any

from rich.table import Table

from agent.interfaces.cli.interactive_input import prompt_value as _prompt_value
from agent.interfaces.cli.ui import console
from agent.interfaces.cli.workspace_entry import workspace_storage_path


def remember(text: str, ctx: Any) -> None:
    parts = text.strip().split(maxsplit=3)
    offset = 2 if len(parts) > 1 and parts[1].casefold() == "remember" else 1
    if len(parts) <= offset + 1:
        console.print("[bold red]Uso: /remember chave valor[/bold red]")
        return
    key, value = parts[offset], parts[offset + 1]
    ctx.orchestrator.remember(key, value)
    console.print(f"[bold green]Lembrei:[/bold green] {key} = {value}")


def show_memory(_: str, ctx: Any) -> None:
    table = Table(title="Memória da Sessão")
    table.add_column("Seção", style="cyan")
    table.add_column("Conteúdo")
    for section, content in ctx.orchestrator.agent_state.memory.state.items():
        if content:
            table.add_row(section, str(content))
    console.print(table)


def forget(_: str, ctx: Any) -> None:
    key = _prompt_value(ctx, "[bold cyan]Chave a esquecer:[/bold cyan] ")
    ctx.orchestrator.forget(key)
    console.print(f"[bold green]Chave '{key}' removida (se existia).[/bold green]")


def clear_memory(_: str, ctx: Any) -> None:
    ctx.orchestrator.clear_memory()
    console.print("[bold green]Memória da sessão limpa.[/bold green]")


def _memory_path(ctx: Any) -> str:
    default = workspace_storage_path(ctx, "memory_file", "agent_memory.json")
    entered = _prompt_value(ctx, f"[bold cyan]Caminho (Enter para '{default}'):[/bold cyan] ", default=str(default))
    return str(entered or default)


def save_memory(_: str, ctx: Any) -> None:
    console.print(f"[bold green]{ctx.orchestrator.save_memory_to_file(_memory_path(ctx))}[/bold green]")


def load_memory(_: str, ctx: Any) -> None:
    console.print(f"[bold green]{ctx.orchestrator.load_memory_from_file(_memory_path(ctx))}[/bold green]")


__all__ = [
    "clear_memory",
    "forget",
    "load_memory",
    "remember",
    "save_memory",
    "show_memory",
]

"""Interactive workspace selection at the CLI application boundary."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agent.runtime.workspace_context import WorkspaceContext


def argument_workspace(args: Any) -> Path:
    return Path(getattr(args, "workspace", Path.cwd())).expanduser()


def canonical_workspace(path: str | Path) -> Path:
    """Resolve and validate one user-selected workspace."""

    return WorkspaceContext.create(path).root


def choose_workspace(*, console: Any, current: str | Path | None = None) -> Path:
    """Choose a workspace without creating directories or changing cwd."""

    current_path = canonical_workspace(current or Path.cwd())
    while True:
        console.print("\n[bold]LLM Agent[/bold]")
        console.print("Workspace:")
        console.print(f"[1] Usar a pasta atual\n    {current_path}")
        console.print("[2] Escolher outra pasta")
        choice = console.input("> ").strip()
        if choice in {"", "1"}:
            return current_path
        if choice != "2":
            console.print("[yellow]Escolha 1 ou 2.[/yellow]")
            continue
        entered = console.input("Pasta do workspace: ").strip()
        if not entered:
            console.print("[yellow]Informe uma pasta existente.[/yellow]")
            continue
        try:
            return canonical_workspace(entered)
        except (FileNotFoundError, NotADirectoryError, PermissionError, ValueError) as exc:
            console.print(f"[red]Workspace inválido:[/red] {exc}")


__all__ = ["argument_workspace", "canonical_workspace", "choose_workspace"]

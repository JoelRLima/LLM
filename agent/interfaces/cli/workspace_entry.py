"""Interactive workspace selection at the CLI application boundary."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, cast

from agent.runtime.workspace_context import WorkspaceContext


class NativePickerUnavailable(RuntimeError):
    """The optional platform-native directory picker cannot be opened."""


def argument_workspace(args: Any) -> Path:
    value = getattr(args, "workspace", None)
    return Path.cwd() if value is None else Path(str(value)).expanduser()


def canonical_workspace(path: str | Path) -> Path:
    """Resolve and validate one user-selected workspace."""

    return cast(Path, WorkspaceContext.create(path).root)


def native_picker_available() -> bool:
    """Return whether the optional Windows picker can be imported."""

    if os.name != "nt":
        return False
    try:
        import tkinter  # noqa: F401
    except (ImportError, OSError):
        return False
    return True


def choose_directory_native() -> Path | None:
    """Open one native Windows directory dialog, returning ``None`` on cancel."""

    if not native_picker_available():
        raise NativePickerUnavailable("Seletor nativo indisponível nesta plataforma.")
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        try:
            root.withdraw()
            root.update_idletasks()
            root.lift()
            root.attributes("-topmost", True)
            selected = filedialog.askdirectory(
                parent=root,
                mustexist=True,
                title="Escolha o workspace do LLM Agent",
            )
        finally:
            root.destroy()
    except Exception as exc:
        raise NativePickerUnavailable("Não foi possível abrir o seletor de pastas.") from exc
    return Path(selected) if selected else None


def _choose_native_workspace(console: Any) -> Path | None:
    try:
        selected = choose_directory_native()
    except NativePickerUnavailable as exc:
        console.print(f"[yellow]{exc} Informe o caminho manualmente.[/yellow]")
        return None
    if selected is None:
        console.print("[yellow]Nenhuma pasta selecionada.[/yellow]")
        return None
    try:
        return canonical_workspace(selected)
    except (FileNotFoundError, NotADirectoryError, PermissionError, ValueError) as exc:
        console.print(f"[red]Workspace inválido:[/red] {exc}")
        return None


def _choose_manual_workspace(console: Any) -> Path | None:
    entered = console.input("Pasta do workspace: ").strip()
    if not entered:
        console.print("[yellow]Informe uma pasta existente.[/yellow]")
        return None
    try:
        return canonical_workspace(entered)
    except (FileNotFoundError, NotADirectoryError, PermissionError, ValueError) as exc:
        console.print(f"[red]Workspace inválido:[/red] {exc}")
        return None


def choose_workspace(*, console: Any, current: str | Path | None = None) -> Path:
    """Choose a workspace without creating directories or changing cwd."""

    current_path = canonical_workspace(current or Path.cwd())
    picker_available = native_picker_available()
    while True:
        console.print("\n[bold]LLM Agent[/bold]")
        console.print("Workspace:")
        console.print(f"[1] Usar a pasta atual\n    {current_path}")
        if picker_available:
            console.print("[2] Procurar pasta...")
            console.print("[3] Informar caminho manualmente")
        else:
            console.print("[2] Informar caminho manualmente")
        choice = console.input("> ").strip()
        if choice in {"", "1"}:
            return current_path
        manual_choice = "3" if picker_available else "2"
        if picker_available and choice == "2":
            selected = _choose_native_workspace(console)
            if selected is not None:
                return selected
            continue
        if choice != manual_choice:
            console.print(
                "[yellow]Escolha 1, 2 ou 3.[/yellow]" if picker_available
                else "[yellow]Escolha 1 ou 2.[/yellow]"
            )
            continue
        selected = _choose_manual_workspace(console)
        if selected is not None:
            return selected


__all__ = [
    "NativePickerUnavailable",
    "argument_workspace",
    "canonical_workspace",
    "choose_directory_native",
    "choose_workspace",
    "native_picker_available",
]

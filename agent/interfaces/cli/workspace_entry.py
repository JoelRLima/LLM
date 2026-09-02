"""Interactive workspace selection at the CLI application boundary."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, cast

from agent.memory.json_persistence import AtomicJsonWriteError, write_json_atomic
from agent.runtime.workspace_context import WorkspaceContext


class NativePickerUnavailable(RuntimeError):
    """The optional platform-native directory picker cannot be opened."""


def argument_workspace(args: Any) -> Path:
    value = getattr(args, "workspace", None)
    return Path.cwd() if value is None else Path(str(value)).expanduser()


def canonical_workspace(path: str | Path) -> Path:
    """Resolve and validate one user-selected workspace."""

    return cast(Path, WorkspaceContext.create(path).root)


def workspace_storage_path(ctx: Any, attribute: str, filename: str) -> str | Path:
    """Return a path supplied by the active workspace authority."""

    workspace_paths = getattr(ctx, "workspace_paths", None)
    if workspace_paths is None:
        raise RuntimeError("workspace storage requires explicit WorkspacePaths")
    selected = getattr(workspace_paths, attribute, None)
    if selected is None:
        raise RuntimeError(
            f"workspace storage authority does not provide {attribute}"
        )
    del filename
    return cast(str | Path, selected)


def load_last_workspace(app_paths: Any) -> Path | None:
    """Load the optional last workspace, failing closed for stale state."""

    path = getattr(app_paths, "last_workspace_file", None)
    if path is None:
        return None
    path = Path(path)
    try:
        if path.is_symlink() or not path.is_file():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        candidate = payload.get("workspace") if isinstance(payload, dict) else None
        if not isinstance(candidate, str) or not candidate.strip():
            return None
        return canonical_workspace(candidate)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None


def remember_workspace(app_paths: Any, workspace: str | Path) -> None:
    """Persist one successfully opened workspace without affecting startup."""

    path = getattr(app_paths, "last_workspace_file", None)
    if path is None:
        return
    try:
        root = canonical_workspace(workspace)
        destination = Path(path)
        write_json_atomic(destination, {"schema_version": 1, "workspace": str(root)})
    except (AtomicJsonWriteError, OSError, TypeError, ValueError):
        # The optional convenience must never make a valid startup fail.
        return


def render_active_workspace(
    console: Any,
    workspace: WorkspaceContext,
    *,
    show_mode_hint: bool = False,
) -> None:
    """Render the canonical workspace owned by the active application."""

    console.print("[bold cyan]Workspace ativo:[/bold cyan]")
    console.print(str(workspace.root), markup=False)
    if show_mode_hint:
        console.print("[dim][READ ONLY] — use /modo para consultar ou alterar o modo[/dim]")


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


def _render_workspace_menu(
    console: Any,
    current_path: Path,
    last_path: Path | None,
    picker_available: bool,
) -> tuple[str | None, str, str | None, str]:
    offset = 1 if last_path is not None else 0
    console.print("\n[bold]LLM Agent[/bold]")
    console.print("Workspace:")
    if last_path is not None:
        console.print(f"[1] Reabrir último diretório\n    {last_path}")
        console.print(f"[2] Usar a pasta atual\n    {current_path}")
    else:
        console.print(f"[1] Usar a pasta atual\n    {current_path}")
    if picker_available:
        console.print(f"[{2 + offset}] Procurar pasta...")
        console.print(f"[{3 + offset}] Informar caminho manualmente")
    else:
        console.print(f"[{2 + offset}] Informar caminho manualmente")
    return (
        "1" if last_path is not None else None,
        "2" if last_path is not None else "1",
        str(2 + offset) if picker_available else None,
        str((3 if picker_available else 2) + offset),
    )


def _invalid_workspace_choice(console: Any, picker_available: bool) -> None:
    message = "Escolha 1, 2 ou 3." if picker_available else "Escolha 1 ou 2."
    console.print(f"[yellow]{message}[/yellow]")


def choose_workspace(
    *,
    console: Any,
    current: str | Path | None = None,
    last_workspace: str | Path | None = None,
) -> Path:
    """Choose a workspace without creating directories or changing cwd."""

    current_path = canonical_workspace(current or Path.cwd())
    last_path: Path | None = None
    if last_workspace is not None:
        try:
            last_path = canonical_workspace(last_workspace)
        except (FileNotFoundError, NotADirectoryError, PermissionError, ValueError):
            last_path = None
    picker_available = native_picker_available()
    while True:
        last_choice, current_choice, native_choice, manual_choice = _render_workspace_menu(
            console, current_path, last_path, picker_available
        )
        choice = console.input("> ").strip()
        if last_choice is not None and choice == last_choice:
            assert last_path is not None
            return last_path
        if choice in {"", current_choice}:
            return current_path
        if native_choice is not None and choice == native_choice:
            selected = _choose_native_workspace(console)
            if selected is not None:
                return selected
            continue
        if choice != manual_choice:
            _invalid_workspace_choice(console, picker_available)
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
    "load_last_workspace",
    "native_picker_available",
    "remember_workspace",
    "render_active_workspace",
]

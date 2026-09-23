"""Interactive workspace selection at the CLI application boundary."""
from __future__ import annotations

import json
import os
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Callable, cast

from agent.memory.json_persistence import AtomicJsonWriteError, write_json_atomic
from agent.runtime.workspace_context import WorkspaceContext


class NativePickerUnavailable(RuntimeError):
    """The optional platform-native directory picker cannot be opened."""
class TaskWorkspaceRequiredError(ValueError):
    """Raised before task-producing CLI bootstrap when selection is absent."""
    reason_code = "TASK_WORKSPACE_REQUIRED"
    def __init__(self) -> None:
        super().__init__("A workspace explícito é obrigatório para executar ou retomar uma tarefa.")
class WorkspaceSelectionCancelled(ValueError):
    """The chooser was cancelled without selecting a workspace."""

    reason_code = "WORKSPACE_SELECTION_CANCELLED"


def argument_workspace(args: Any) -> Path:
    value = getattr(args, "workspace", None)
    return Path.cwd() if value is None else Path(str(value)).expanduser()
def require_task_workspace(args: Any) -> Path:
    """Return only an explicitly supplied task workspace path."""
    value = getattr(args, "workspace", None)
    if value is None or not str(value).strip():
        raise TaskWorkspaceRequiredError()
    return Path(str(value)).expanduser()
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
        from agent.interfaces.cli.workspace_recents import remember_recent_workspace
        remember_recent_workspace(app_paths, root)
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
    console.print(f"Workspace: {workspace.root}", markup=False)
    if show_mode_hint:
        console.print("[dim]READ ONLY · use /modo para consultar ou alterar o modo[/dim]")
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


def _choose_manual_workspace(
    console: Any,
    prompt: Callable[[str], str | None] | None,
    *,
    cancel_on_blank: bool,
) -> Path | None:
    if prompt is None:
        from agent.interfaces.cli.interactive_shell import prompt_from

        raw = prompt_from(console, "Pasta do workspace: ")
    else:
        raw = prompt("Pasta do workspace: ")
    entered = (raw or "").strip()
    if not entered:
        if cancel_on_blank:
            raise WorkspaceSelectionCancelled()
        console.print("[yellow]Informe uma pasta existente.[/yellow]")
        return None
    try:
        return canonical_workspace(entered)
    except (FileNotFoundError, NotADirectoryError, PermissionError, ValueError) as exc:
        console.print(f"[red]Workspace inválido:[/red] {exc}")
        return None


def _read_workspace_choice(
    console: Any,
    prompt: Callable[[str], str | None] | None,
    numbered_count: int,
) -> str:
    if prompt is None:
        from agent.interfaces.cli.interactive_shell import prompt_from

        raw_choice = prompt_from(console, "> ")
    else:
        raw_choice = prompt("> ")
    choice = (raw_choice or "").strip()
    return str(int(choice)) if choice.isdecimal() and 1 <= int(choice) <= numbered_count else choice


def _recent_workspace_paths(
    current_path: Path,
    last_path: Path | None,
    recent_workspaces: Iterable[str | Path],
) -> list[Path]:
    values = list(recent_workspaces)
    if last_path is not None:
        values.append(last_path)
    paths: list[Path] = []
    for value in values:
        try:
            candidate = canonical_workspace(value)
        except (FileNotFoundError, NotADirectoryError, PermissionError, ValueError):
            continue
        if candidate == current_path or candidate in paths:
            continue
        paths.append(candidate)
        if len(paths) >= 8:
            break
    return paths


def _render_workspace_choices(
    console: Any, choices: list[tuple[str, Path]], native_choice: str | None, manual_choice: str
) -> None:
    console.print("\n[bold]LLM Agent[/bold]")
    console.print("Workspace:")
    for number, (label, path) in enumerate(choices, start=1):
        console.print(f"[{number}] {label}\n    {path}")
    if native_choice is not None:
        console.print(f"[{native_choice}] Procurar pasta...")
    console.print(f"[{manual_choice}] Informar caminho manualmente")


def _choose_from_workspace_paths(
    *,
    console: Any,
    choices: list[tuple[str, Path]],
    picker_available: bool,
    prompt: Callable[[str], str | None] | None,
    path_prompt: Callable[[str], str | None] | None,
    cancel_on_blank: bool,
) -> Path:
    current_path = next(path for label, path in choices if label == "Usar a pasta atual")
    numbered_paths = {str(number): path for number, (_, path) in enumerate(choices, start=1)}
    native_choice = str(len(choices) + 1) if picker_available else None
    manual_choice = str(len(choices) + (2 if picker_available else 1))
    while True:
        _render_workspace_choices(console, choices, native_choice, manual_choice)

        choice = _read_workspace_choice(console, prompt, len(choices))
        if not choice:
            if cancel_on_blank:
                raise WorkspaceSelectionCancelled()
            return current_path
        if choice in numbered_paths:
            return numbered_paths[choice]
        if choice == native_choice:
            selected = _choose_native_workspace(console)
            if selected is not None:
                return selected
            if cancel_on_blank:
                raise WorkspaceSelectionCancelled()
            continue
        if choice == manual_choice:
            selected = _choose_manual_workspace(console, path_prompt or prompt, cancel_on_blank=cancel_on_blank)
            if selected is not None:
                return selected
            continue
        console.print(f"[yellow]Escolha 1, 2 ou {3 if picker_available else 2}.[/yellow]")


def choose_workspace(
    *,
    console: Any,
    current: str | Path | None = None,
    last_workspace: str | Path | None = None,
    recent_workspaces: Iterable[str | Path] | None = None,
    prompt: Callable[[str], str | None] | None = None,
    path_prompt: Callable[[str], str | None] | None = None,
) -> Path:
    """Choose and validate a workspace at the canonical CLI boundary."""

    current_path = canonical_workspace(current or Path.cwd())
    try:
        last_path = canonical_workspace(last_workspace) if last_workspace is not None else None
    except (FileNotFoundError, NotADirectoryError, PermissionError, ValueError):
        last_path = None
    choices = [("Usar a pasta atual", current_path)]
    if recent_workspaces is None and last_path is not None:
        choices.insert(0, ("Reabrir último diretório", last_path))
    if recent_workspaces is not None:
        choices.extend(
            ("Reabrir último diretório", path)
            for path in _recent_workspace_paths(current_path, last_path, recent_workspaces)
        )
    return _choose_from_workspace_paths(
        console=console,
        choices=choices,
        picker_available=native_picker_available(),
        prompt=prompt,
        path_prompt=path_prompt if recent_workspaces is not None else None,
        cancel_on_blank=recent_workspaces is not None,
    )


__all__ = [
    "NativePickerUnavailable",
    "TaskWorkspaceRequiredError",
    "WorkspaceSelectionCancelled",
    "argument_workspace",
    "canonical_workspace",
    "choose_directory_native",
    "choose_workspace",
    "load_last_workspace",
    "native_picker_available",
    "require_task_workspace",
    "remember_workspace",
    "render_active_workspace",
]

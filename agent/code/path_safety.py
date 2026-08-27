"""Path confinement primitives shared by code-assistance use cases."""

from __future__ import annotations

from pathlib import Path

from agent.runtime.filesystem_primitives import is_link_like


class WorkspacePathError(ValueError):
    """Raised when a code-assistance path crosses the workspace boundary."""


def resolve_workspace_path(
    root: str | Path,
    value: str | Path,
    *,
    require_file: bool = False,
    require_directory: bool = False,
) -> Path:
    """Resolve ``value`` without allowing an existing symlink to escape ``root``."""

    workspace = Path(root).resolve()
    try:
        candidate = (workspace / Path(value)).resolve()
        candidate.relative_to(workspace)
    except (OSError, RuntimeError, ValueError) as exc:
        raise WorkspacePathError(f"Caminho fora do workspace: {value}") from exc
    if require_file and not candidate.is_file():
        raise FileNotFoundError(str(value))
    if require_directory and not candidate.is_dir():
        raise NotADirectoryError(str(value))
    return candidate


def workspace_relative_path(root: str | Path, value: str | Path) -> str:
    """Return the canonical, POSIX-style workspace-relative representation."""

    workspace = Path(root).resolve()
    return resolve_workspace_path(workspace, value).relative_to(workspace).as_posix()


def workspace_command_argument(root: str | Path, value: str | Path) -> str:
    """Normalize a filesystem argument and protect it from option injection."""

    relative = workspace_relative_path(root, value)
    return "." if relative == "." else f"./{relative}"


__all__ = [
    "WorkspacePathError",
    "is_link_like",
    "resolve_workspace_path",
    "workspace_command_argument",
    "workspace_relative_path",
]

"""Canonical workspace path confinement primitives."""

from __future__ import annotations

from pathlib import Path

from agent.runtime.filesystem_primitives import (
    FinalPathInspection,
    inspect_final_path,
    is_link_like,
)


class WorkspacePathError(ValueError):
    """Raised when a path crosses the workspace boundary."""


def assert_no_link_ancestors(path: str | Path) -> None:
    """Reject link-like components on the lexical path, without following them."""

    current = Path(path).expanduser()
    while True:
        inspection = inspect_final_path(current)
        if inspection.is_link_like:
            raise WorkspacePathError(f"Caminho link-like não permitido: {current}")
        parent = current.parent
        if parent == current:
            return
        current = parent


def assert_path_safe(
    path: str | Path,
    *,
    directory: bool | None = None,
) -> FinalPathInspection:
    """Inspect one path and its ancestors before a caller touches it."""

    candidate = Path(path).expanduser()
    assert_no_link_ancestors(candidate)
    inspection = inspect_final_path(candidate)
    if inspection.is_link_like:
        raise WorkspacePathError(f"Caminho link-like não permitido: {candidate}")
    if not inspection.exists:
        return inspection
    if directory is True and not candidate.is_dir():
        raise NotADirectoryError(str(candidate))
    if directory is False and not candidate.is_file():
        raise IsADirectoryError(str(candidate))
    return inspection


def resolve_path(value: str | Path, *, reject_link_like: bool = False) -> Path:
    """Resolve a path after optionally validating every existing component."""

    candidate = Path(value).expanduser()
    if reject_link_like:
        assert_no_link_ancestors(candidate)
    try:
        return candidate.resolve()
    except (OSError, RuntimeError) as exc:
        raise WorkspacePathError(f"Caminho não pôde ser resolvido: {value}") from exc


def assert_owned_path(root: str | Path, child: str | Path) -> Path:
    """Validate confinement and link safety for one child of an owned root."""

    root_path = Path(root).expanduser()
    raw_child = Path(child).expanduser()
    child_path = raw_child if raw_child.is_absolute() else root_path / raw_child
    assert_path_safe(root_path, directory=True)
    assert_no_link_ancestors(child_path)
    try:
        selected = resolve_workspace_path(root_path, raw_child)
    except WorkspacePathError:
        raise
    assert_path_safe(child_path)
    return selected


def assert_no_link_descendants(root: str | Path) -> None:
    """Fail closed when an owned tree contains any link-like descendant."""

    root_path = Path(root).expanduser()
    inspection = assert_path_safe(root_path)
    if not inspection.exists:
        return
    if not root_path.is_dir():
        raise NotADirectoryError(str(root_path))
    try:
        descendants = root_path.rglob("*")
        for descendant in descendants:
            assert_path_safe(descendant)
    except (OSError, RuntimeError) as exc:
        if isinstance(exc, WorkspacePathError):
            raise
        raise WorkspacePathError(f"Caminho descendente não pôde ser inspecionado: {root_path}") from exc


def resolve_workspace_path(
    root: str | Path,
    value: str | Path,
    *,
    require_file: bool = False,
    require_directory: bool = False,
) -> Path:
    """Resolve ``value`` without allowing an existing symlink to escape ``root``.

    This primitive intentionally treats ``~`` literally. Boundaries that
    accept user-home shorthand must apply ``Path(value).expanduser()`` before
    calling it, so input normalization remains explicit and cannot silently
    reinterpret a shorthand as a workspace-relative filename.
    """

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
    "assert_no_link_ancestors",
    "assert_no_link_descendants",
    "assert_owned_path",
    "assert_path_safe",
    "is_link_like",
    "resolve_path",
    "resolve_workspace_path",
    "workspace_command_argument",
    "workspace_relative_path",
]

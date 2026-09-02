"""Skill-facing path error adapter.

The canonical workspace-confinement implementation lives in
``agent.runtime.path_safety``. These helpers preserve the historical tuple
return shape used by the file-oriented skills while delegating all path
resolution and confinement to that owner.

This module is an adapter, not a second confinement owner.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Tuple

from agent.runtime.path_safety import WorkspacePathError, resolve_workspace_path


def resolve_safe_path(base_dir: Path, relative_path: str) -> Tuple[Optional[Path], Optional[str]]:
    """Return the canonical path plus the skill-compatible error string."""
    try:
        return resolve_workspace_path(base_dir, relative_path), None
    except WorkspacePathError:
        return None, f"Acesso fora do diretório seguro: {relative_path}"
    except (OSError, RuntimeError, ValueError) as exc:
        return None, f"Caminho inválido: {relative_path} ({exc})"


def resolve_confined_file(base_dir: Path, candidate: Path) -> Optional[Path]:
    """Resolve one discovered file through the canonical path owner."""

    try:
        return resolve_workspace_path(base_dir, candidate, require_file=True)
    except (OSError, RuntimeError, ValueError):
        return None

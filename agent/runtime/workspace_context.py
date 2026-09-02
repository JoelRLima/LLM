"""Explicit workspace identity and safe path resolution."""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path

from agent.runtime.path_safety import WorkspacePathError, resolve_workspace_path


def _workspace_id(root: Path) -> str:
    normalized = os.path.normcase(str(root))
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]
    label = re.sub(r"[^a-zA-Z0-9_-]+", "-", root.name).strip("-") or "workspace"
    return f"{label[:32]}-{digest}"


@dataclass(frozen=True)
class WorkspaceContext:
    """Canonical workspace root supplied by an application boundary."""

    root: Path
    workspace_id: str

    @classmethod
    def create(
        cls,
        root: str | Path,
        *,
        require_exists: bool = True,
    ) -> "WorkspaceContext":
        resolved = Path(root).expanduser().resolve()
        if require_exists and not resolved.exists():
            raise FileNotFoundError(f"Workspace não encontrado: {resolved}")
        if resolved.exists() and not resolved.is_dir():
            raise NotADirectoryError(f"Workspace não é um diretório: {resolved}")
        return cls(root=resolved, workspace_id=_workspace_id(resolved))

    def resolve(self, path: str | Path, *, must_exist: bool = False) -> Path:
        try:
            requested = Path(path).expanduser()
            candidate = resolve_workspace_path(self.root, requested)
        except WorkspacePathError as exc:
            raise PermissionError(f"Caminho fora do workspace: {path}") from exc
        if must_exist and not candidate.exists():
            raise FileNotFoundError(f"Caminho não encontrado no workspace: {path}")
        return candidate

    def relative(self, path: str | Path) -> Path:
        return self.resolve(path).relative_to(self.root)

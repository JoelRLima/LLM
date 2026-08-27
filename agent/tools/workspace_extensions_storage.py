"""Atomic storage for one workspace's extension configuration."""

from __future__ import annotations

import stat
from pathlib import Path
from typing import cast

from agent.runtime.filesystem_primitives import is_link_like
from agent.tools.extension_catalog_errors import (
    CatalogStorageError,
    WorkspaceConfigurationCorruptError,
    WorkspaceStorageError,
)
from agent.tools.extension_catalog_storage import ExtensionCatalogStorage
from agent.tools.extension_state import WorkspaceExtensionsState
from agent.tools.workspace_extensions_codec import (
    decode_workspace_extensions,
    encode_workspace_extensions,
)


class WorkspaceExtensionsStorage:
    """Reuse Gate 2.2 atomic primitives with a workspace-specific codec."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._atomic = ExtensionCatalogStorage(self.path)

    def load(self) -> WorkspaceExtensionsState:
        if is_link_like(self.path):
            raise WorkspaceStorageError("Configuração do workspace não pode ser symlink")
        if not self.path.exists():
            return WorkspaceExtensionsState()
        try:
            return cast(WorkspaceExtensionsState, decode_workspace_extensions(self.path.read_bytes()))
        except (OSError, CatalogStorageError) as exc:
            raise WorkspaceStorageError(
                "Falha ao ler configuração do workspace.",
                secondary_errors=getattr(exc, "secondary_errors", ()),
            ) from exc
        except Exception as exc:
            if isinstance(exc, (WorkspaceStorageError, WorkspaceConfigurationCorruptError)):
                raise
            raise WorkspaceConfigurationCorruptError(
                "Configuração do workspace corrompida."
            ) from exc

    def save(self, state: WorkspaceExtensionsState) -> None:
        if is_link_like(self.path):
            raise WorkspaceStorageError("Configuração do workspace não pode ser symlink")
        try:
            payload = encode_workspace_extensions(state)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            existing_mode: int | None = None
            if self.path.exists():
                existing_mode = stat.S_IMODE(self.path.stat().st_mode)
            self._atomic._save_atomically(payload, existing_mode)
        except CatalogStorageError as exc:
            raise WorkspaceStorageError(
                "Falha ao salvar configuração do workspace.",
                secondary_errors=getattr(exc, "secondary_errors", ()),
            ) from exc
        except OSError as exc:
            raise WorkspaceStorageError("Falha ao salvar configuração do workspace.") from exc


__all__ = ["WorkspaceExtensionsStorage"]

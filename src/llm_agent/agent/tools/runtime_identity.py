"""Shared identity for one immutable application bootstrap snapshot."""

from __future__ import annotations

import uuid
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RuntimeSnapshotIdentity:
    """Opaque correlation identity shared by registry and authority."""

    snapshot_id: str
    workspace_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.snapshot_id, str) or not self.snapshot_id.strip():
            raise ValueError("snapshot_id deve ser uma string não vazia")
        if not isinstance(self.workspace_id, str) or not self.workspace_id.strip():
            raise ValueError("workspace_id deve ser uma string não vazia")
        if any(token in self.workspace_id for token in ("/", "\\", "..")):
            raise ValueError("workspace_id não pode conter caminho")

    @classmethod
    def create(cls, workspace_id: str) -> "RuntimeSnapshotIdentity":
        return cls(snapshot_id=str(uuid.uuid4()), workspace_id=workspace_id)

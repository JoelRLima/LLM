"""Stable checkpoint constants and load errors."""

from __future__ import annotations

from pathlib import Path

CHECKPOINT_SCHEMA_VERSION = 2


class CheckpointLoadError(RuntimeError):
    """A present checkpoint cannot be resumed safely."""

    code = "CHECKPOINT_INVALID"

    def __init__(
        self,
        path: str | Path,
        detail: str,
        *,
        reason_code: str | None = None,
    ) -> None:
        self.path = Path(path)
        self.detail = str(detail)
        self.reason_code = reason_code or self.code
        super().__init__(f"Checkpoint inválido em {self.path}: {self.detail}")


__all__ = ["CHECKPOINT_SCHEMA_VERSION", "CheckpointLoadError"]

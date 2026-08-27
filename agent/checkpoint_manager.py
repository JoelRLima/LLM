"""
agent/checkpoint_manager.py

CheckpointManager is the single owner of checkpoint-file I/O.  The state
object remains the owner of the serialized task fields; this module owns the
durability, path-safety, and load-disposition boundary.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any, Dict, Optional, cast

from agent.checkpoint_types import CHECKPOINT_SCHEMA_VERSION, CheckpointLoadError
from agent.checkpoint_validation import validate_document
from agent.memory.json_persistence import write_json_atomic
from agent.memory.path_safety import LinkLikePathError, inspect_final_path, reject_link_like
from agent.runtime.filesystem_primitives import sync_parent_directory
from agent.runtime.lock_filesystem import open_verified
from agent.runtime.logging import logger


class CheckpointManager:
    """Save, load, and remove one task checkpoint."""

    def __init__(self, checkpoint_file: str | Path):
        self.checkpoint_file = str(checkpoint_file)

    def save(self, agent_state: Any) -> bool:
        """Persist a versioned checkpoint with an atomic, link-safe replace.

        Return ``True`` only after the replacement and parent sync succeed.
        A failed write never replaces the previous valid checkpoint.
        """

        destination = Path(self.checkpoint_file)
        try:
            checkpoint_data = agent_state.to_checkpoint_dict()
            if not isinstance(checkpoint_data, dict):
                raise TypeError("serialização do checkpoint não produziu um objeto")
            checkpoint_data["schema_version"] = CHECKPOINT_SCHEMA_VERSION

            # The shared primitive owns temporary-file durability, final
            # link checks, atomic replacement, and parent-directory sync.
            write_json_atomic(destination, checkpoint_data, default=str)
            return True
        except Exception as exc:
            logger.warning("Falha ao salvar checkpoint: %s", exc)
            return False

    def load(self) -> Optional[Dict[str, Any]]:
        """Load a checkpoint, distinguishing absence from invalid state.

        ``None`` means that no checkpoint exists.  A present but corrupt,
        incompatible, or structurally unsafe checkpoint raises
        :class:`CheckpointLoadError`; callers must expose that as an explicit
        non-success and must not silently start a fresh task.
        """

        destination = Path(self.checkpoint_file)
        try:
            inspection = inspect_final_path(destination)
        except OSError as exc:
            raise CheckpointLoadError(destination, f"falha ao inspecionar o arquivo: {exc}") from exc
        if not inspection.exists:
            return None

        try:
            data = self._read_json(destination)
        except CheckpointLoadError:
            raise
        except Exception as exc:
            raise CheckpointLoadError(
                destination,
                f"arquivo corrompido ou ilegível: {exc}",
                reason_code="CHECKPOINT_CORRUPT",
            ) from exc

        validate_document(destination, data)
        return data

    @staticmethod
    def _read_json(path: Path) -> Dict[str, Any]:
        descriptor: int | None = None
        try:
            descriptor = open_verified(
                path,
                os.O_RDONLY | getattr(os, "O_BINARY", 0),
            )
            with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
                descriptor = None
                return cast(Dict[str, Any], json.load(stream))
        finally:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
    def delete(self) -> None:
        """Remove only the configured regular checkpoint file."""

        destination = Path(self.checkpoint_file)
        try:
            inspection = reject_link_like(destination)
            if not inspection.exists:
                return
            if inspection.metadata is None or not stat.S_ISREG(inspection.metadata.st_mode):
                logger.warning("Checkpoint não é um arquivo regular; preservado: %s", destination)
                return
            os.unlink(destination)
            sync_parent_directory(destination)
        except (LinkLikePathError, OSError) as exc:
            logger.warning("Falha ao remover checkpoint: %s", exc)


__all__ = [
    "CHECKPOINT_SCHEMA_VERSION",
    "CheckpointLoadError",
    "CheckpointManager",
]

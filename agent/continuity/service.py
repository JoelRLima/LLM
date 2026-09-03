"""Read-only deterministic classification of the workspace checkpoint."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent.checkpoint_manager import CheckpointLoadError, CheckpointManager
from agent.continuity.checkpoint_projection import (
    REASON_CHECKPOINT_ABSENT,
    REASON_CHECKPOINT_INVALID,
    REASON_CHECKPOINT_INVALID_CONTINUITY,
    REASON_CHECKPOINT_INVALID_TERMINAL_DISPOSITION,
    REASON_CHECKPOINT_RESUMABLE,
    REASON_CHECKPOINT_ROOT_MISSING,
    REASON_HIERARCHICAL_RESUME_UNSUPPORTED,
    REASON_TASK_ALREADY_TERMINAL,
    REASON_TASK_DEFINITION_BINDING_MISSING,
    REASON_TASK_DEFINITION_INCOMPLETE,
    REASON_TASK_PAUSED,
    absent_snapshot,
    classify_checkpoint_document,
    invalid_snapshot,
)
from agent.continuity.snapshot import TaskContinuitySnapshot


class TaskContinuityService:
    """Project one workspace checkpoint without execution-side effects."""

    def __init__(self, workspace_paths: Any, *, checkpoint_manager: CheckpointManager | None = None) -> None:
        self.workspace_paths = workspace_paths
        self.workspace_id = _workspace_id(workspace_paths)
        if checkpoint_manager is None:
            checkpoint_file = getattr(workspace_paths, "checkpoint_file", None)
            if checkpoint_file is None:
                checkpoint_file = workspace_paths
            if checkpoint_file is None:
                raise ValueError("continuity service requires checkpoint paths")
            checkpoint_manager = CheckpointManager(checkpoint_file)
        self.checkpoint_manager = checkpoint_manager

    def snapshot(self) -> TaskContinuitySnapshot:
        """Read and classify the canonical checkpoint exactly once."""

        try:
            checkpoint = self.checkpoint_manager.load()
        except CheckpointLoadError as exc:
            return self._invalid(_reason_code(exc))
        except Exception:
            # A custom/compatibility manager must not turn a read failure into
            # a fresh-task interpretation.  The canonical manager normally
            # converts filesystem and JSON failures to CheckpointLoadError.
            return self._invalid(REASON_CHECKPOINT_INVALID)
        if checkpoint is None:
            return absent_snapshot(self.workspace_id)
        return classify_checkpoint_document(checkpoint, workspace_id=self.workspace_id)

    def classify_checkpoint(self, checkpoint: Any) -> TaskContinuitySnapshot:
        """Classify an already-loaded checkpoint without performing I/O."""

        return classify_checkpoint_document(checkpoint, workspace_id=self.workspace_id)

    def status(self) -> TaskContinuitySnapshot:
        """Compatibility spelling for callers that ask the service for status."""

        return self.snapshot()

    def classify(self) -> TaskContinuitySnapshot:
        """Return the same deterministic read projection as :meth:`snapshot`."""

        return self.snapshot()

    def _project(self, checkpoint: Mapping[str, Any]) -> TaskContinuitySnapshot:
        return self.classify_checkpoint(checkpoint)

    def _invalid(self, reason_code: str, *, checkpoint_schema_version: int | None = None) -> TaskContinuitySnapshot:
        return invalid_snapshot(
            self.workspace_id,
            reason_code,
            checkpoint_schema_version=checkpoint_schema_version,
        )
def _workspace_id(workspace_paths: Any) -> str:
    value = getattr(workspace_paths, "workspace_id", None)
    if not isinstance(value, str) or not value.strip():
        return "workspace"
    selected = value.strip()
    return selected[:253] + "..." if len(selected) > 256 else selected


def _reason_code(error: CheckpointLoadError) -> str:
    selected = getattr(error, "reason_code", None)
    if not isinstance(selected, str) or not selected.strip():
        return REASON_CHECKPOINT_INVALID
    return selected.strip()
__all__ = [
    "classify_checkpoint_document",
    "REASON_CHECKPOINT_ABSENT",
    "REASON_CHECKPOINT_INVALID",
    "REASON_CHECKPOINT_INVALID_CONTINUITY",
    "REASON_CHECKPOINT_INVALID_TERMINAL_DISPOSITION",
    "REASON_CHECKPOINT_RESUMABLE",
    "REASON_CHECKPOINT_ROOT_MISSING",
    "REASON_HIERARCHICAL_RESUME_UNSUPPORTED",
    "REASON_TASK_ALREADY_TERMINAL",
    "REASON_TASK_DEFINITION_BINDING_MISSING",
    "REASON_TASK_DEFINITION_INCOMPLETE",
    "REASON_TASK_PAUSED",
    "TaskContinuityService",
]

"""Pure checkpoint-to-continuity projection helpers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent.checkpoint_types import CHECKPOINT_SCHEMA_VERSION
from agent.continuity.models import (
    CONTINUITY_SNAPSHOT_SCHEMA_VERSION,
    ContinuityMetadata,
    RelatedRun,
    TaskContinuityStatus,
)
from agent.continuity.progress import project_plan_progress
from agent.continuity.snapshot import (
    TaskContinuitySnapshot,
    TaskDefinitionRefSummary,
)
from agent.planning.task_completion_types import CompletionDisposition
from agent.runtime.outcome_taxonomy import NON_SUCCESS_STATUSES, OperationalStatus
from agent.runtime.task_directives import (
    ABSENT,
    validate_checkpoint_task_run_directive,
)

REASON_CHECKPOINT_ABSENT = "CHECKPOINT_ABSENT"
REASON_CHECKPOINT_RESUMABLE = "CHECKPOINT_RESUMABLE"
REASON_TASK_PAUSED = "TASK_PAUSED"
REASON_TASK_ALREADY_TERMINAL = "TASK_ALREADY_TERMINAL"
REASON_HIERARCHICAL_RESUME_UNSUPPORTED = "HIERARCHICAL_RESUME_UNSUPPORTED"
REASON_CHECKPOINT_INVALID = "CHECKPOINT_INVALID"
REASON_CHECKPOINT_INVALID_TERMINAL_DISPOSITION = "CHECKPOINT_INVALID_TERMINAL_DISPOSITION"
REASON_CHECKPOINT_INVALID_CONTINUITY = "CHECKPOINT_INVALID_CONTINUITY"
REASON_CHECKPOINT_ROOT_MISSING = "CHECKPOINT_ROOT_TASK_ID_MISSING"
REASON_TASK_DEFINITION_BINDING_MISSING = "TASK_DEFINITION_BINDING_MISSING"
REASON_TASK_DEFINITION_INCOMPLETE = "TASK_DEFINITION_INCOMPLETE"

_TERMINAL_DISPOSITIONS = (
    frozenset(item.value for item in CompletionDisposition)
    | NON_SUCCESS_STATUSES
    | {OperationalStatus.SUCCEEDED.value}
)
_HIERARCHICAL_STATUSES = frozenset({"inactive", "running", "completed"})
_REASONS = {
    REASON_CHECKPOINT_ABSENT: "Nenhum checkpoint existe nesta workspace.",
    REASON_CHECKPOINT_RESUMABLE: "O checkpoint valido pode ser retomado.",
    REASON_TASK_PAUSED: "A tarefa foi interrompida e pode ser retomada.",
    REASON_TASK_ALREADY_TERMINAL: "A tarefa ja terminou e nao pode ser retomada.",
    REASON_CHECKPOINT_ROOT_MISSING: "O checkpoint nao possui root_task_id; a retomada nao e segura.",
    REASON_TASK_DEFINITION_BINDING_MISSING: (
        "O checkpoint nao possui uma vinculacao TaskDefinitionRef; a retomada nao e segura."
    ),
    REASON_TASK_DEFINITION_INCOMPLETE: (
        "A definicao da tarefa ainda nao esta completa; a retomada nao e segura."
    ),
    REASON_HIERARCHICAL_RESUME_UNSUPPORTED: (
        "A execucao hierarquica em andamento nao pode ser retomada com seguranca."
    ),
}


class _ProjectionError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


def classify_checkpoint_document(
    checkpoint: Mapping[str, Any] | None,
    *,
    workspace_id: str = "workspace",
) -> TaskContinuitySnapshot:
    """Classify one loaded checkpoint without constructing execution owners."""

    selected_workspace_id = _workspace_id(workspace_id)
    if checkpoint is None:
        return absent_snapshot(selected_workspace_id)
    if not isinstance(checkpoint, Mapping):
        return invalid_snapshot(selected_workspace_id, REASON_CHECKPOINT_INVALID)
    try:
        return _project_checkpoint(checkpoint, selected_workspace_id)
    except _ProjectionError as exc:
        return invalid_snapshot(
            selected_workspace_id,
            exc.reason_code,
            checkpoint_schema_version=_schema_version(checkpoint),
        )
    except Exception:
        return invalid_snapshot(
            selected_workspace_id,
            REASON_CHECKPOINT_INVALID,
            checkpoint_schema_version=_schema_version(checkpoint),
        )


def _project_checkpoint(
    checkpoint: Mapping[str, Any],
    workspace_id: str,
) -> TaskContinuitySnapshot:
    schema_version = _required_non_negative_int(checkpoint.get("schema_version"), "schema_version")
    if schema_version != CHECKPOINT_SCHEMA_VERSION:
        raise _ProjectionError("CHECKPOINT_INCOMPATIBLE_SCHEMA")
    objective = checkpoint.get("objective")
    if not isinstance(objective, str) or not objective.strip():
        raise _ProjectionError(REASON_CHECKPOINT_INVALID)
    root_task_id = _optional_text(checkpoint.get("root_task_id"), "root_task_id")
    terminal = _terminal_disposition(checkpoint.get("terminal_disposition"))
    raw_directive = (
        checkpoint["task_run_directive"]
        if "task_run_directive" in checkpoint
        else ABSENT
    )
    try:
        validate_checkpoint_task_run_directive(
            objective=objective,
            raw=raw_directive,
            plan_present=bool(checkpoint.get("plan")),
            terminal_disposition=terminal,
            materialize=False,
        )
    except (TypeError, ValueError) as exc:
        raise _ProjectionError(REASON_CHECKPOINT_INVALID) from exc
    hierarchical_status = _hierarchical_status(checkpoint.get("hierarchical_lifecycle"))
    continuity = _continuity(checkpoint.get("continuity"))
    task_definition = _task_definition(checkpoint.get("task_definition"))
    if (
        task_definition is not None
        and root_task_id is not None
        and root_task_id != task_definition.task_id
    ):
        raise _ProjectionError(REASON_CHECKPOINT_INVALID)
    try:
        progress = project_plan_progress(checkpoint)
    except (TypeError, ValueError) as exc:
        raise _ProjectionError(REASON_CHECKPOINT_INVALID) from exc
    related_runs = _related_runs(continuity, root_task_id)
    status, reason_code = _classify_status(
        terminal,
        hierarchical_status,
        continuity,
        root_task_id=root_task_id,
        task_definition=task_definition,
    )
    return TaskContinuitySnapshot(
        schema_version=CONTINUITY_SNAPSHOT_SCHEMA_VERSION,
        workspace_id=workspace_id,
        status=status,
        reason_code=reason_code,
        resumable=status in {TaskContinuityStatus.RESUMABLE, TaskContinuityStatus.PAUSED},
        checkpoint_present=True,
        checkpoint_schema_version=schema_version,
        objective_preview=objective,
        root_task_id=root_task_id,
        task_definition_ref=task_definition,
        terminal_disposition=terminal,
        hierarchical_status=hierarchical_status,
        plan_progress=progress,
        continuity=continuity,
        related_runs=related_runs,
        reason=_REASONS.get(reason_code, reason_code),
    )


def _terminal_disposition(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise _ProjectionError(REASON_CHECKPOINT_INVALID_TERMINAL_DISPOSITION)
    selected = value.strip()
    if selected not in _TERMINAL_DISPOSITIONS:
        raise _ProjectionError(REASON_CHECKPOINT_INVALID_TERMINAL_DISPOSITION)
    return selected


def _hierarchical_status(value: Any) -> str:
    if value is None:
        return "inactive"
    if not isinstance(value, Mapping):
        raise _ProjectionError(REASON_CHECKPOINT_INVALID)
    selected = value.get("status", "inactive")
    if not isinstance(selected, str) or selected not in _HIERARCHICAL_STATUSES:
        raise _ProjectionError(REASON_CHECKPOINT_INVALID)
    return selected


def _continuity(value: Any) -> ContinuityMetadata | None:
    try:
        return ContinuityMetadata.from_mapping(value)
    except (TypeError, ValueError) as exc:
        reason_code = (
            "CHECKPOINT_INCOMPATIBLE_CONTINUITY_SCHEMA"
            if "schema" in str(exc).casefold()
            else REASON_CHECKPOINT_INVALID_CONTINUITY
        )
        raise _ProjectionError(reason_code) from exc


def _task_definition(value: Any) -> TaskDefinitionRefSummary | None:
    if value is None:
        return None
    try:
        return TaskDefinitionRefSummary.from_mapping(value)
    except (TypeError, ValueError, KeyError) as exc:
        raise _ProjectionError(REASON_CHECKPOINT_INVALID) from exc


def _classify_status(
    terminal: str | None,
    hierarchical_status: str,
    continuity: ContinuityMetadata | None,
    *,
    root_task_id: str | None,
    task_definition: TaskDefinitionRefSummary | None,
) -> tuple[TaskContinuityStatus, str]:
    if root_task_id is None:
        return TaskContinuityStatus.INVALID, REASON_CHECKPOINT_ROOT_MISSING
    if task_definition is None:
        return TaskContinuityStatus.INVALID, REASON_TASK_DEFINITION_BINDING_MISSING
    if task_definition.definition_state != "complete":
        return TaskContinuityStatus.UNSUPPORTED, REASON_TASK_DEFINITION_INCOMPLETE
    if terminal is not None:
        return TaskContinuityStatus.TERMINAL, REASON_TASK_ALREADY_TERMINAL
    if hierarchical_status == "running":
        return TaskContinuityStatus.UNSUPPORTED, REASON_HIERARCHICAL_RESUME_UNSUPPORTED
    if continuity is not None and continuity.interrupted:
        return TaskContinuityStatus.PAUSED, REASON_TASK_PAUSED
    return TaskContinuityStatus.RESUMABLE, REASON_CHECKPOINT_RESUMABLE


def absent_snapshot(workspace_id: str) -> TaskContinuitySnapshot:
    return TaskContinuitySnapshot(
        schema_version=CONTINUITY_SNAPSHOT_SCHEMA_VERSION,
        workspace_id=workspace_id,
        status=TaskContinuityStatus.ABSENT,
        reason_code=REASON_CHECKPOINT_ABSENT,
        resumable=False,
        checkpoint_present=False,
        checkpoint_schema_version=None,
        reason=_REASONS[REASON_CHECKPOINT_ABSENT],
    )


def invalid_snapshot(
    workspace_id: str,
    reason_code: str,
    *,
    checkpoint_schema_version: int | None = None,
) -> TaskContinuitySnapshot:
    return TaskContinuitySnapshot(
        schema_version=CONTINUITY_SNAPSHOT_SCHEMA_VERSION,
        workspace_id=workspace_id,
        status=TaskContinuityStatus.INVALID,
        reason_code=reason_code,
        resumable=False,
        checkpoint_present=True,
        checkpoint_schema_version=checkpoint_schema_version,
        reason=reason_code,
    )


def _related_runs(
    continuity: ContinuityMetadata | None,
    root_task_id: str | None,
) -> tuple[RelatedRun, ...]:
    if continuity is None:
        return ()
    run_ids = [continuity.last_run_id, continuity.resumed_from_run_id]
    return tuple(
        RelatedRun(run_id=run_id, root_task_id=root_task_id, liveness="unavailable")
        for run_id in run_ids
        if isinstance(run_id, str) and run_id.strip()
    )


def _workspace_id(value: Any) -> str:
    selected = getattr(value, "workspace_id", value)
    if not isinstance(selected, str) or not selected.strip():
        return "workspace"
    selected = selected.strip()
    return selected[:253] + "..." if len(selected) > 256 else selected


def _schema_version(checkpoint: Mapping[str, Any]) -> int | None:
    value = checkpoint.get("schema_version")
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _required_non_negative_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _ProjectionError(REASON_CHECKPOINT_INVALID)
    return value


def _optional_text(value: Any, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise _ProjectionError(REASON_CHECKPOINT_INVALID)
    return value.strip()


__all__ = [
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
    "absent_snapshot",
    "classify_checkpoint_document",
    "invalid_snapshot",
]

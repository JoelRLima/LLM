"""Snapshot and task-definition projections for continuity status."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from agent.continuity.models import (
    CONTINUITY_SNAPSHOT_SCHEMA_VERSION,
    MAX_CONTINUITY_TEXT,
    MAX_OBJECTIVE_PREVIEW,
    MAX_REASON,
    MAX_RELATED_RUNS,
    ContinuityMetadata,
    PlanProgress,
    RelatedRun,
    TaskContinuityStatus,
    _bounded_text,
    _optional_text,
)


@dataclass(frozen=True, slots=True)
class TaskDefinitionRefSummary:
    """Safe projection of the compact task-definition binding."""

    task_id: str
    contract_version: int
    contract_digest: str
    definition_state: str
    spec_version: int | None = None
    spec_digest: str | None = None
    active_phase_id: str | None = None

    @classmethod
    def from_mapping(cls, value: Any) -> "TaskDefinitionRefSummary":
        from agent.task_definition.models import TaskDefinitionRef

        if isinstance(value, cls):
            return value
        reference = value if isinstance(value, TaskDefinitionRef) else TaskDefinitionRef.from_dict(value)
        return cls(
            task_id=reference.task_id,
            contract_version=reference.contract_version,
            contract_digest=reference.contract_digest,
            definition_state=reference.definition_state,
            spec_version=reference.spec_version,
            spec_digest=reference.spec_digest,
            active_phase_id=reference.active_phase_id,
        )

    from_dict = from_mapping

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _bounded_text(self.task_id, MAX_CONTINUITY_TEXT, "task_id", required=True))
        if (
            isinstance(self.contract_version, bool)
            or not isinstance(self.contract_version, int)
            or self.contract_version < 0
        ):
            raise ValueError("contract_version must be a non-negative integer")
        object.__setattr__(
            self,
            "contract_digest",
            _bounded_text(self.contract_digest, MAX_CONTINUITY_TEXT, "contract_digest", required=True),
        )
        object.__setattr__(
            self,
            "definition_state",
            _bounded_text(self.definition_state, MAX_CONTINUITY_TEXT, "definition_state", required=True),
        )
        if self.spec_version is not None and (
            isinstance(self.spec_version, bool)
            or not isinstance(self.spec_version, int)
            or self.spec_version < 0
        ):
            raise ValueError("spec_version must be a non-negative integer or null")
        object.__setattr__(self, "spec_digest", _optional_text(self.spec_digest, MAX_CONTINUITY_TEXT, "spec_digest"))
        object.__setattr__(self, "active_phase_id", _optional_text(self.active_phase_id, MAX_CONTINUITY_TEXT, "active_phase_id"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "contract_version": self.contract_version,
            "contract_digest": self.contract_digest,
            "definition_state": self.definition_state,
            "spec_version": self.spec_version,
            "spec_digest": self.spec_digest,
            "active_phase_id": self.active_phase_id,
        }


TaskDefinitionSummary = TaskDefinitionRefSummary


@dataclass(frozen=True, slots=True)
class TaskContinuitySnapshot:
    """Immutable UI-neutral continuity projection."""

    schema_version: int
    workspace_id: str
    status: TaskContinuityStatus
    reason_code: str
    resumable: bool
    checkpoint_present: bool
    checkpoint_schema_version: int | None
    objective_preview: str | None = None
    root_task_id: str | None = None
    task_definition_ref: TaskDefinitionRefSummary | None = None
    terminal_disposition: str | None = None
    hierarchical_status: str | None = None
    plan_progress: PlanProgress = field(default_factory=PlanProgress)
    continuity: ContinuityMetadata | None = None
    related_runs: tuple[RelatedRun, ...] = ()
    reason: str | None = None

    def __post_init__(self) -> None:
        selected_status = _normalize_header(self)
        _validate_checkpoint_facts(self, selected_status)
        _normalize_projection(self)

    @property
    def resume_generation(self) -> int:
        return self.continuity.resume_generation if self.continuity is not None else 0

    @property
    def is_resumable(self) -> bool:
        return self.resumable

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "workspace_id": self.workspace_id,
            "status": self.status.value,
            "reason_code": self.reason_code,
            "reason": self.reason,
            "resumable": self.resumable,
            "checkpoint_present": self.checkpoint_present,
            "checkpoint_schema_version": self.checkpoint_schema_version,
            "objective_preview": self.objective_preview,
            "root_task_id": self.root_task_id,
            "task_definition_ref": self.task_definition_ref.to_dict() if self.task_definition_ref is not None else None,
            "terminal_disposition": self.terminal_disposition,
            "hierarchical_status": self.hierarchical_status,
            "plan_progress": self.plan_progress.to_dict(),
            "continuity": self.continuity.to_dict() if self.continuity is not None else None,
            "resume_generation": self.resume_generation,
            "related_runs": [item.to_dict() for item in self.related_runs],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, allow_nan=False, sort_keys=True)


def _normalize_header(snapshot: TaskContinuitySnapshot) -> TaskContinuityStatus:
    if isinstance(snapshot.schema_version, bool) or snapshot.schema_version != CONTINUITY_SNAPSHOT_SCHEMA_VERSION:
        raise ValueError("unsupported continuity snapshot schema")
    object.__setattr__(
        snapshot,
        "workspace_id",
        _bounded_text(snapshot.workspace_id, MAX_CONTINUITY_TEXT, "workspace_id", required=True),
    )
    try:
        selected_status = TaskContinuityStatus(snapshot.status)
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid continuity status") from exc
    object.__setattr__(snapshot, "status", selected_status)
    object.__setattr__(
        snapshot,
        "reason_code",
        _bounded_text(snapshot.reason_code, MAX_REASON, "reason_code", required=True),
    )
    return selected_status


def _validate_checkpoint_facts(
    snapshot: TaskContinuitySnapshot,
    selected_status: TaskContinuityStatus,
) -> None:
    if not isinstance(snapshot.resumable, bool):
        raise TypeError("resumable must be a boolean")
    expected_resumable = selected_status in {TaskContinuityStatus.RESUMABLE, TaskContinuityStatus.PAUSED}
    if snapshot.resumable != expected_resumable:
        raise ValueError("resumable does not match continuity status")
    if not isinstance(snapshot.checkpoint_present, bool):
        raise TypeError("checkpoint_present must be a boolean")
    if selected_status is TaskContinuityStatus.ABSENT:
        if snapshot.checkpoint_present or snapshot.checkpoint_schema_version is not None:
            raise ValueError("absent continuity cannot contain checkpoint facts")
    elif not snapshot.checkpoint_present:
        raise ValueError("non-absent continuity requires a checkpoint")
    if snapshot.checkpoint_schema_version is not None and (
        isinstance(snapshot.checkpoint_schema_version, bool)
        or not isinstance(snapshot.checkpoint_schema_version, int)
        or snapshot.checkpoint_schema_version < 0
    ):
        raise ValueError("checkpoint schema version is invalid")


def _normalize_projection(snapshot: TaskContinuitySnapshot) -> None:
    object.__setattr__(snapshot, "objective_preview", _optional_text(snapshot.objective_preview, MAX_OBJECTIVE_PREVIEW, "objective_preview"))
    object.__setattr__(snapshot, "root_task_id", _optional_text(snapshot.root_task_id, MAX_CONTINUITY_TEXT, "root_task_id"))
    if snapshot.task_definition_ref is not None and not isinstance(snapshot.task_definition_ref, TaskDefinitionRefSummary):
        object.__setattr__(snapshot, "task_definition_ref", TaskDefinitionRefSummary.from_mapping(snapshot.task_definition_ref))
    object.__setattr__(snapshot, "terminal_disposition", _optional_text(snapshot.terminal_disposition, MAX_CONTINUITY_TEXT, "terminal_disposition"))
    object.__setattr__(snapshot, "hierarchical_status", _optional_text(snapshot.hierarchical_status, MAX_CONTINUITY_TEXT, "hierarchical_status"))
    if not isinstance(snapshot.plan_progress, PlanProgress):
        object.__setattr__(snapshot, "plan_progress", PlanProgress(**dict(snapshot.plan_progress)))
    if snapshot.continuity is not None and not isinstance(snapshot.continuity, ContinuityMetadata):
        object.__setattr__(snapshot, "continuity", ContinuityMetadata.from_mapping(snapshot.continuity))
    selected_runs = () if snapshot.related_runs is None else tuple(snapshot.related_runs)
    object.__setattr__(
        snapshot,
        "related_runs",
        tuple(item if isinstance(item, RelatedRun) else RelatedRun.from_mapping(item) for item in selected_runs[:MAX_RELATED_RUNS]),
    )
    selected_reason = snapshot.reason if snapshot.reason is not None else snapshot.reason_code
    object.__setattr__(snapshot, "reason", _bounded_text(selected_reason, MAX_REASON, "reason", required=True))


__all__ = [
    "TaskContinuitySnapshot",
    "TaskDefinitionRefSummary",
    "TaskDefinitionSummary",
]

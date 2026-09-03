"""Immutable, bounded read models for task continuity status."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Mapping

CONTINUITY_METADATA_SCHEMA_VERSION = 1
CONTINUITY_SNAPSHOT_SCHEMA_VERSION = 1
MAX_OBJECTIVE_PREVIEW = 240
MAX_REASON = 512
MAX_RELATED_RUNS = 8
MAX_CONTINUITY_TEXT = 512
MAX_CONTINUITY_TIMESTAMP = 128


class TaskContinuityStatus(str, Enum):
    """Deterministic disposition of the workspace's single checkpoint slot."""

    ABSENT = "absent"
    RESUMABLE = "resumable"
    PAUSED = "paused"
    TERMINAL = "terminal"
    UNSUPPORTED = "unsupported"
    INVALID = "invalid"


def _bounded_text(value: str, limit: int, name: str, *, required: bool = False) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    selected = value.strip()
    if required and not selected:
        raise ValueError(f"{name} must be non-empty")
    if len(selected) <= limit:
        return selected
    return selected[: max(0, limit - 3)] + "..."


def _optional_text(value: Any, limit: int, name: str) -> str | None:
    if value is None:
        return None
    return _bounded_text(value, limit, name, required=True)


def _timestamp(value: Any, name: str) -> str:
    selected = _bounded_text(value, MAX_CONTINUITY_TIMESTAMP, name, required=True)
    try:
        datetime.fromisoformat(selected.replace("Z", "+00:00"))
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be a parseable timestamp") from exc
    return selected


@dataclass(frozen=True, slots=True)
class ContinuityMetadata:
    """Optional bounded metadata describing an intentional interruption."""

    schema_version: int = CONTINUITY_METADATA_SCHEMA_VERSION
    resume_generation: int = 0
    last_run_id: str | None = None
    interrupted: bool = False
    interruption_reason: str | None = None
    interrupted_at: str | None = None
    resumed_from_run_id: str | None = None

    @classmethod
    def from_mapping(cls, value: Any) -> "ContinuityMetadata | None":
        if value is None:
            return None
        if isinstance(value, cls):
            return value
        if not isinstance(value, Mapping):
            raise ValueError("continuity metadata must be an object")
        allowed = {
            "schema_version",
            "resume_generation",
            "last_run_id",
            "interrupted",
            "interruption_reason",
            "interrupted_at",
            "resumed_from_run_id",
        }
        if any(key not in allowed for key in value):
            raise ValueError("continuity metadata contains unknown fields")
        required = ("schema_version", "resume_generation", "last_run_id", "interrupted")
        if any(key not in value for key in required):
            raise ValueError("continuity metadata is incomplete")
        return cls(
            schema_version=value["schema_version"],
            resume_generation=value["resume_generation"],
            last_run_id=value["last_run_id"],
            interrupted=value["interrupted"],
            interruption_reason=value.get("interruption_reason"),
            interrupted_at=value.get("interrupted_at"),
            resumed_from_run_id=value.get("resumed_from_run_id"),
        )

    from_dict = from_mapping

    def __post_init__(self) -> None:
        if isinstance(self.schema_version, bool) or self.schema_version != CONTINUITY_METADATA_SCHEMA_VERSION:
            raise ValueError("unsupported continuity metadata schema")
        if (
            isinstance(self.resume_generation, bool)
            or not isinstance(self.resume_generation, int)
            or self.resume_generation < 0
        ):
            raise ValueError("continuity resume_generation must be non-negative")
        object.__setattr__(
            self,
            "last_run_id",
            _optional_text(self.last_run_id, MAX_CONTINUITY_TEXT, "last_run_id"),
        )
        if not isinstance(self.interrupted, bool):
            raise TypeError("continuity interrupted must be a boolean")
        object.__setattr__(
            self,
            "interruption_reason",
            _optional_text(self.interruption_reason, MAX_REASON, "interruption_reason"),
        )
        object.__setattr__(
            self,
            "interrupted_at",
            None if self.interrupted_at is None else _timestamp(self.interrupted_at, "interrupted_at"),
        )
        object.__setattr__(
            self,
            "resumed_from_run_id",
            _optional_text(self.resumed_from_run_id, MAX_CONTINUITY_TEXT, "resumed_from_run_id"),
        )
        if not self.interrupted and (
            self.interruption_reason is not None or self.interrupted_at is not None
        ):
            raise ValueError("non-interrupted continuity cannot contain interruption details")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "resume_generation": self.resume_generation,
            "last_run_id": self.last_run_id,
            "interrupted": self.interrupted,
            "interruption_reason": self.interruption_reason,
            "interrupted_at": self.interrupted_at,
            "resumed_from_run_id": self.resumed_from_run_id,
        }


@dataclass(frozen=True, slots=True)
class PlanProgress:
    """Small projection of persisted plan cursor and step-record progress."""

    total_steps: int = 0
    completed_steps: int = 0
    pending_steps: int = 0
    current_step: int | None = None
    current_step_id: str | None = None

    def __post_init__(self) -> None:
        for name in ("total_steps", "completed_steps", "pending_steps"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if self.completed_steps > self.total_steps or self.pending_steps > self.total_steps:
            raise ValueError("plan progress counts exceed total steps")
        if self.current_step is not None and (
            isinstance(self.current_step, bool)
            or not isinstance(self.current_step, int)
            or self.current_step < 0
        ):
            raise ValueError("current_step must be a non-negative integer or null")
        object.__setattr__(
            self,
            "current_step_id",
            _optional_text(self.current_step_id, MAX_CONTINUITY_TEXT, "current_step_id"),
        )

    @property
    def total(self) -> int:
        return self.total_steps

    @property
    def completed(self) -> int:
        return self.completed_steps

    @property
    def pending(self) -> int:
        return self.pending_steps

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_steps": self.total_steps,
            "completed_steps": self.completed_steps,
            "pending_steps": self.pending_steps,
            "current_step": self.current_step,
            "current_step_id": self.current_step_id,
        }


@dataclass(frozen=True, slots=True)
class RelatedRun:
    """Optional bounded run-lineage item for observability enrichment."""

    run_id: str
    root_task_id: str | None = None
    liveness: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", _bounded_text(self.run_id, MAX_CONTINUITY_TEXT, "run_id", required=True))
        object.__setattr__(
            self,
            "root_task_id",
            _optional_text(self.root_task_id, MAX_CONTINUITY_TEXT, "root_task_id"),
        )
        object.__setattr__(self, "liveness", _optional_text(self.liveness, MAX_CONTINUITY_TEXT, "liveness"))

    @classmethod
    def from_mapping(cls, value: Any) -> "RelatedRun":
        if isinstance(value, cls):
            return value
        if not isinstance(value, Mapping):
            raise ValueError("related run must be an object")
        return cls(
            run_id=value["run_id"],
            root_task_id=value.get("root_task_id"),
            liveness=value.get("liveness"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "root_task_id": self.root_task_id,
            "liveness": self.liveness,
        }


def __getattr__(name: str) -> Any:
    """Keep the original model import surface without a module cycle."""

    if name in {"TaskContinuitySnapshot", "TaskDefinitionRefSummary", "TaskDefinitionSummary"}:
        from agent.continuity.snapshot import (
            TaskContinuitySnapshot,
            TaskDefinitionRefSummary,
            TaskDefinitionSummary,
        )

        return {
            "TaskContinuitySnapshot": TaskContinuitySnapshot,
            "TaskDefinitionRefSummary": TaskDefinitionRefSummary,
            "TaskDefinitionSummary": TaskDefinitionSummary,
        }[name]
    raise AttributeError(name)


__all__ = [
    "CONTINUITY_METADATA_SCHEMA_VERSION",
    "CONTINUITY_SNAPSHOT_SCHEMA_VERSION",
    "ContinuityMetadata",
    "MAX_CONTINUITY_TEXT",
    "MAX_CONTINUITY_TIMESTAMP",
    "MAX_OBJECTIVE_PREVIEW",
    "MAX_REASON",
    "MAX_RELATED_RUNS",
    "PlanProgress",
    "RelatedRun",
    "TaskContinuityStatus",
]

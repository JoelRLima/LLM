"""Immutable, bounded presentation contracts.

This module intentionally contains no terminal, Rich, model, tool, approval, or
authority objects.  It is a read model over redacted observation envelopes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from agent.observability.liveness import TraceLivenessPolicy
from agent.observability.redaction import (
    MAX_OBSERVATION_TEXT,
    canonical_json,
    freeze_observation_value,
    redact_observation_value,
    redact_text,
    unfreeze_observation_value,
)
from agent.presentation.query import (
    MAX_QUERY_LIMIT,
    MAX_SEARCH_CHARS,
    InspectionQuery,
    _bounded_int,
)

MAX_BOOKMARKS = 256


@dataclass(frozen=True, slots=True)
class RunSummary:
    """Safe run identity/status information projected from trace metadata."""

    run_id: str
    root_task_id: str
    start_time: str
    end_time: str | None
    active: bool
    status: str
    completeness: str
    mode: str
    final_outcome: Mapping[str, Any] | None
    highest_sequence: int
    semantic_count: int
    diagnostic_count: int
    gap_count: int
    dropped_count: int
    suppressed_count: int
    liveness: Mapping[str, Any] = field(default_factory=lambda: unavailable_section("liveness unavailable"))

    def __post_init__(self) -> None:
        for name in ("run_id", "root_task_id", "start_time", "status", "completeness", "mode"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ValueError(f"run summary {name} is invalid")
        for name in (
            "highest_sequence",
            "semantic_count",
            "diagnostic_count",
            "gap_count",
            "dropped_count",
            "suppressed_count",
        ):
            _bounded_int(getattr(self, name), f"run summary {name}")
        if not isinstance(self.active, bool):
            raise TypeError("run summary active must be a boolean")
        if self.final_outcome is not None:
            object.__setattr__(self, "final_outcome", freeze_observation_value(redact_observation_value(self.final_outcome)))
        object.__setattr__(self, "liveness", freeze_observation_value(redact_observation_value(self.liveness)))

    @classmethod
    def from_metadata(
        cls,
        metadata: Any,
        *,
        now: Any = None,
        liveness_policy: TraceLivenessPolicy | None = None,
    ) -> "RunSummary":
        policy = liveness_policy or TraceLivenessPolicy()
        return cls(
            run_id=str(metadata.run_id),
            root_task_id=str(metadata.root_task_id),
            start_time=str(metadata.start_time),
            end_time=metadata.end_time,
            active=bool(metadata.active),
            status=str(metadata.status),
            completeness=metadata.completeness.value,
            mode=metadata.observability_mode.value,
            final_outcome=metadata.final_outcome,
            highest_sequence=int(metadata.highest_sequence_persisted),
            semantic_count=int(metadata.semantic_count),
            diagnostic_count=int(metadata.diagnostic_count),
            gap_count=int(metadata.gap_count),
            dropped_count=int(metadata.dropped_count),
            suppressed_count=int(metadata.suppressed_count),
            liveness=policy.evaluate(metadata, now).to_dict(),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "root_task_id": self.root_task_id,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "active": self.active,
            "status": self.status,
            "completeness": self.completeness,
            "mode": self.mode,
            "final_outcome": unfreeze_observation_value(self.final_outcome),
            "highest_sequence": self.highest_sequence,
            "semantic_count": self.semantic_count,
            "diagnostic_count": self.diagnostic_count,
            "gap_count": self.gap_count,
            "dropped_count": self.dropped_count,
            "suppressed_count": self.suppressed_count,
            "liveness": unfreeze_observation_value(self.liveness),
        }


@dataclass(frozen=True, slots=True)
class Activity:
    """A bounded UI-neutral activity row."""

    sequence: int
    timestamp: str
    source: str
    category: str
    kind: str
    severity: str | None
    status: str | None
    title: str
    summary: str
    run_id: str
    root_task_id: str | None = None
    task_id: str | None = None
    parent_task_id: str | None = None
    node_id: str | None = None
    plan_id: str | None = None
    step_id: str | None = None
    invocation_id: str | None = None
    detail_available: bool = True
    active: bool = False
    terminal: bool = False
    gap: bool = False
    bookmarked: bool = False
    data: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _bounded_int(self.sequence, "activity sequence", minimum=1)
        for name in ("timestamp", "source", "category", "kind", "title", "summary", "run_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ValueError(f"activity {name} is invalid")
        for name in (
            "severity",
            "status",
            "root_task_id",
            "task_id",
            "parent_task_id",
            "node_id",
            "plan_id",
            "step_id",
            "invocation_id",
        ):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value):
                raise ValueError(f"activity {name} is invalid")
        for name in ("detail_available", "active", "terminal", "gap", "bookmarked"):
            if not isinstance(getattr(self, name), bool):
                raise TypeError(f"activity {name} must be a boolean")
        object.__setattr__(self, "title", redact_text(self.title, limit=96))
        object.__setattr__(self, "summary", redact_text(self.summary, limit=MAX_OBSERVATION_TEXT))
        object.__setattr__(self, "data", freeze_observation_value(redact_observation_value(self.data)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "timestamp": self.timestamp,
            "source": self.source,
            "category": self.category,
            "kind": self.kind,
            "severity": self.severity,
            "status": self.status,
            "title": self.title,
            "summary": self.summary,
            "run_id": self.run_id,
            "root_task_id": self.root_task_id,
            "task_id": self.task_id,
            "parent_task_id": self.parent_task_id,
            "node_id": self.node_id,
            "plan_id": self.plan_id,
            "step_id": self.step_id,
            "invocation_id": self.invocation_id,
            "detail_available": self.detail_available,
            "active": self.active,
            "terminal": self.terminal,
            "gap": self.gap,
            "bookmarked": self.bookmarked,
            "data": unfreeze_observation_value(self.data),
        }


def unavailable_section(reason: str = "canonical source unavailable") -> dict[str, str]:
    """Return an explicit unavailable section instead of an inferred value."""

    return {"status": "unavailable", "reason": redact_text(str(reason), limit=256)}


@dataclass(frozen=True, slots=True)
class InspectorSnapshot:
    """Immutable structured snapshot consumed by all interface adapters."""

    run: RunSummary
    current: Mapping[str, Any]
    timeline: tuple[Activity, ...]
    model_calls: Mapping[str, Any]
    tools: Mapping[str, Any]
    validation: Mapping[str, Any]
    recovery: Mapping[str, Any]
    changes: Mapping[str, Any]
    metrics: Mapping[str, Any]
    warnings: tuple[Activity, ...]
    heartbeat: Mapping[str, Any]
    selected_detail: Mapping[str, Any] | None
    query: InspectionQuery
    bookmarks: tuple[Mapping[str, Any], ...] = ()
    issues: tuple[str, ...] = ()
    plan_steps: Mapping[str, Any] = field(default_factory=unavailable_section)

    def __post_init__(self) -> None:
        for name in (
            "current",
            "plan_steps",
            "model_calls",
            "tools",
            "validation",
            "recovery",
            "changes",
            "metrics",
            "heartbeat",
        ):
            object.__setattr__(self, name, freeze_observation_value(redact_observation_value(getattr(self, name))))
        if self.selected_detail is not None:
            object.__setattr__(self, "selected_detail", freeze_observation_value(redact_observation_value(self.selected_detail)))
        object.__setattr__(self, "bookmarks", tuple(freeze_observation_value(redact_observation_value(item)) for item in self.bookmarks[:MAX_BOOKMARKS]))
        object.__setattr__(self, "issues", tuple(redact_text(str(item), limit=256) for item in self.issues[:64]))

    @property
    def completeness(self) -> str:
        return self.run.completeness

    def to_dict(self) -> dict[str, Any]:
        return {
            "run": self.run.to_dict(),
            "current": unfreeze_observation_value(self.current),
            "plan_steps": unfreeze_observation_value(self.plan_steps),
            "timeline": [item.to_dict() for item in self.timeline],
            "model_calls": unfreeze_observation_value(self.model_calls),
            "tools": unfreeze_observation_value(self.tools),
            "validation": unfreeze_observation_value(self.validation),
            "recovery": unfreeze_observation_value(self.recovery),
            "changes": unfreeze_observation_value(self.changes),
            "metrics": unfreeze_observation_value(self.metrics),
            "warnings": [item.to_dict() for item in self.warnings],
            "heartbeat": unfreeze_observation_value(self.heartbeat),
            "selected_detail": unfreeze_observation_value(self.selected_detail),
            "query": self.query.to_dict(),
            "bookmarks": [unfreeze_observation_value(item) for item in self.bookmarks],
            "issues": list(self.issues),
        }

    def to_json(self) -> str:
        return canonical_json(self.to_dict())


__all__ = [
    "Activity",
    "InspectionQuery",
    "InspectorSnapshot",
    "MAX_QUERY_LIMIT",
    "MAX_SEARCH_CHARS",
    "RunSummary",
    "unavailable_section",
]

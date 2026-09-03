"""Immutable, bounded diagnostic facts kept outside semantic runtime truth."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping

from agent.observability.diagnostic_normalization import (
    merge_correlation as merge_diagnostic_correlation,
)
from agent.observability.diagnostic_normalization import (
    normalized_data as normalize_diagnostic_data,
)
from agent.observability.diagnostic_normalization import (
    normalized_message as normalize_diagnostic_message,
)
from agent.observability.diagnostic_normalization import (
    selected_correlation as resolve_diagnostic_correlation,
)
from agent.observability.modes import ObservabilityMode
from agent.observability.redaction import (
    MAX_OBSERVATION_DATA_CHARS,
    MAX_OBSERVATION_TEXT,
    canonical_json,
    freeze_observation_value,
    unfreeze_observation_value,
)
from agent.runtime.correlation import RunCorrelation

MAX_DIAGNOSTIC_MESSAGE = MAX_OBSERVATION_TEXT
MAX_DIAGNOSTIC_DATA_CHARS = MAX_OBSERVATION_DATA_CHARS
DIAGNOSTIC_SCHEMA_VERSION = 1


class DiagnosticSeverity(str, Enum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"

    @classmethod
    def parse(cls, value: Any) -> "DiagnosticSeverity":
        if isinstance(value, cls):
            return value
        if not isinstance(value, str):
            raise ValueError("diagnostic severity must be DEBUG, INFO, WARNING, or ERROR")
        normalized = value.strip().casefold()
        try:
            return cls(normalized)
        except ValueError as exc:
            raise ValueError("diagnostic severity must be DEBUG, INFO, WARNING, or ERROR") from exc


class DiagnosticCategory(str, Enum):
    PIPELINE_HEALTH = "pipeline_health"
    SINK_FAILURE = "sink_failure"
    QUEUE_PRESSURE = "queue_pressure"
    HEARTBEAT = "heartbeat"
    TRACE_STORAGE = "trace_storage"
    INSPECTOR = "inspector"
    LIFECYCLE = "lifecycle"
    TRANSPORT = "transport"
    RETENTION = "retention"
    COMPATIBILITY = "compatibility"
    INTERNAL = "internal"

    @classmethod
    def parse(cls, value: Any) -> str:
        if isinstance(value, cls):
            return value.value
        if not isinstance(value, str) or not value.strip():
            raise ValueError("diagnostic category must be a non-empty string")
        normalized = value.strip().casefold().replace("-", "_").replace(" ", "_")
        if len(normalized) > 96:
            raise ValueError("diagnostic category is too long")
        return normalized


_MINIMUM_MODE_BY_CATEGORY = {
    DiagnosticCategory.PIPELINE_HEALTH.value: ObservabilityMode.NORMAL,
    DiagnosticCategory.SINK_FAILURE.value: ObservabilityMode.NORMAL,
    DiagnosticCategory.QUEUE_PRESSURE.value: ObservabilityMode.NORMAL,
    DiagnosticCategory.HEARTBEAT.value: ObservabilityMode.NORMAL,
    DiagnosticCategory.TRACE_STORAGE.value: ObservabilityMode.NORMAL,
    DiagnosticCategory.INSPECTOR.value: ObservabilityMode.VERBOSE,
    DiagnosticCategory.LIFECYCLE.value: ObservabilityMode.VERBOSE,
    DiagnosticCategory.TRANSPORT.value: ObservabilityMode.DEBUG,
    DiagnosticCategory.RETENTION.value: ObservabilityMode.DEBUG,
    DiagnosticCategory.COMPATIBILITY.value: ObservabilityMode.DEBUG,
    DiagnosticCategory.INTERNAL.value: ObservabilityMode.DEBUG,
}


def _optional_id(value: Any, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string or null")
    if len(value) > 256:
        raise ValueError(f"{name} is too long")
    return value


def _timestamp(value: Any) -> str:
    if value is None:
        return datetime.now(timezone.utc).isoformat()
    if isinstance(value, datetime):
        return value.isoformat()
    if not isinstance(value, str) or not value.strip():
        raise ValueError("diagnostic timestamp must be a non-empty string or datetime")
    if len(value) > 128:
        raise ValueError("diagnostic timestamp is too long")
    return value


def _selected_kind(
    category: str | DiagnosticCategory | None,
    kind: str | DiagnosticCategory | None,
) -> str | DiagnosticCategory:
    selected = kind if kind is not None else category
    if selected is None:
        raise ValueError("diagnostic category/kind is required")
    if kind is not None and category is not None:
        if DiagnosticCategory.parse(kind) != DiagnosticCategory.parse(category):
            raise ValueError("diagnostic category and kind conflict")
    return selected


def _selected_message(message: str | None, summary: str | None) -> str | None:
    if message is not None and summary is not None and message != summary:
        raise ValueError("diagnostic message and summary conflict")
    return message if message is not None else summary


@dataclass(frozen=True, slots=True, init=False)
class DiagnosticRecord:
    """A non-semantic, immutable diagnostic fact.

    ``kind`` is the canonical serialized category.  ``category`` and
    ``summary`` are accepted as ergonomic aliases because callers often use
    the vocabulary from the specification.
    """

    schema_version: int
    kind: str
    severity: DiagnosticSeverity
    timestamp: str
    run_id: str | None
    root_task_id: str | None
    task_id: str | None
    parent_task_id: str | None
    node_id: str | None
    plan_id: str | None
    step_id: str | None
    invocation_id: str | None
    data: Mapping[str, Any]
    message: str | None
    minimum_mode: ObservabilityMode

    def __init__(
        self,
        category: str | DiagnosticCategory | None = None,
        severity: DiagnosticSeverity | str = DiagnosticSeverity.INFO,
        timestamp: str | datetime | None = None,
        data: Mapping[str, Any] | None = None,
        message: str | None = None,
        *,
        kind: str | DiagnosticCategory | None = None,
        summary: str | None = None,
        schema_version: int = DIAGNOSTIC_SCHEMA_VERSION,
        run_id: str | None = None,
        root_task_id: str | None = None,
        task_id: str | None = None,
        parent_task_id: str | None = None,
        node_id: str | None = None,
        plan_id: str | None = None,
        step_id: str | None = None,
        invocation_id: str | None = None,
        correlation: RunCorrelation | Mapping[str, Any] | None = None,
        minimum_mode: ObservabilityMode | str = ObservabilityMode.NORMAL,
    ) -> None:
        if isinstance(schema_version, bool) or not isinstance(schema_version, int) or schema_version < 1:
            raise ValueError("diagnostic schema_version must be a positive integer")
        selected_kind = _selected_kind(category, kind)
        selected_message = _selected_message(message, summary)
        selected_correlation = resolve_diagnostic_correlation(correlation)
        values: dict[str, Any] = {
            "run_id": run_id,
            "root_task_id": root_task_id,
            "task_id": task_id,
            "parent_task_id": parent_task_id,
            "node_id": node_id,
            "plan_id": plan_id,
            "step_id": step_id,
            "invocation_id": invocation_id,
        }
        merge_diagnostic_correlation(values, selected_correlation)
        normalized_data = normalize_diagnostic_data(data)
        normalized_message = normalize_diagnostic_message(selected_message)
        object.__setattr__(self, "schema_version", schema_version)
        object.__setattr__(self, "kind", DiagnosticCategory.parse(selected_kind))
        object.__setattr__(self, "severity", DiagnosticSeverity.parse(severity))
        object.__setattr__(self, "timestamp", _timestamp(timestamp))
        for name, value in values.items():
            object.__setattr__(self, name, _optional_id(value, name))
        object.__setattr__(self, "data", freeze_observation_value(normalized_data))
        object.__setattr__(self, "message", normalized_message)
        object.__setattr__(self, "minimum_mode", ObservabilityMode.parse(minimum_mode))

    @property
    def category(self) -> str:
        return self.kind

    @property
    def summary(self) -> str | None:
        return self.message

    def is_allowed_in(self, mode: ObservabilityMode | str) -> bool:
        selected = ObservabilityMode.parse(mode)
        required = self.minimum_mode
        category_required = _MINIMUM_MODE_BY_CATEGORY.get(self.kind, required)
        if category_required.rank > required.rank:
            required = category_required
        return selected.allows_diagnostic(required)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "type": "diagnostic",
            "kind": self.kind,
            "severity": self.severity.value,
            "timestamp": self.timestamp,
            "run_id": self.run_id,
            "root_task_id": self.root_task_id,
            "task_id": self.task_id,
            "parent_task_id": self.parent_task_id,
            "node_id": self.node_id,
            "plan_id": self.plan_id,
            "step_id": self.step_id,
            "invocation_id": self.invocation_id,
            "data": unfreeze_observation_value(self.data),
            "message": self.message,
            "minimum_mode": self.minimum_mode.value,
        }

    def to_json(self) -> str:
        return canonical_json(self.to_dict())

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "DiagnosticRecord":
        if not isinstance(value, Mapping):
            raise TypeError("diagnostic record must be an object")
        return cls(
            kind=value.get("kind", value.get("category")),
            severity=value.get("severity", DiagnosticSeverity.INFO.value),
            timestamp=value.get("timestamp"),
            data=value.get("data") if isinstance(value.get("data"), Mapping) else {},
            message=value.get("message", value.get("summary")),
            schema_version=value.get("schema_version", DIAGNOSTIC_SCHEMA_VERSION),
            run_id=value.get("run_id"),
            root_task_id=value.get("root_task_id"),
            task_id=value.get("task_id"),
            parent_task_id=value.get("parent_task_id"),
            node_id=value.get("node_id"),
            plan_id=value.get("plan_id"),
            step_id=value.get("step_id"),
            invocation_id=value.get("invocation_id"),
            minimum_mode=value.get("minimum_mode", ObservabilityMode.NORMAL.value),
        )


def minimum_mode_for_category(category: str | DiagnosticCategory) -> ObservabilityMode:
    normalized = DiagnosticCategory.parse(category)
    return _MINIMUM_MODE_BY_CATEGORY.get(normalized, ObservabilityMode.NORMAL)


__all__ = [
    "DIAGNOSTIC_SCHEMA_VERSION",
    "DiagnosticCategory",
    "DiagnosticRecord",
    "DiagnosticSeverity",
    "MAX_DIAGNOSTIC_DATA_CHARS",
    "MAX_DIAGNOSTIC_MESSAGE",
    "minimum_mode_for_category",
]

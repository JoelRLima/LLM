"""Public trace metadata, completeness and retention contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from agent.observability.envelopes import ObservationEnvelope
from agent.observability.modes import ObservabilityMode
from agent.observability.redaction import REDACTION_POLICY_VERSION
from agent.observability.trace_paths import (
    TRACE_STORE_SCHEMA_VERSION,
    TraceCorruptError,
)


class TraceCompleteness(str, Enum):
    ACTIVE = "active"
    COMPLETE = "complete"
    PARTIAL = "partial"
    UNCLEAN = "unclean"
    CORRUPT = "corrupt"

    @property
    def display_name(self) -> str:
        return self.name


CompletenessStatus = TraceCompleteness
TraceStatus = TraceCompleteness


def _active_from_metadata(value: Mapping[str, Any], completeness: TraceCompleteness) -> bool:
    raw_active = value.get("active", completeness is TraceCompleteness.ACTIVE)
    if not isinstance(raw_active, bool):
        raise ValueError("metadata active is invalid")
    return raw_active


@dataclass(frozen=True, slots=True)
class TraceMetadata:
    schema_version: int
    run_id: str
    root_task_id: str
    start_time: str
    end_time: str | None
    observability_mode: ObservabilityMode
    status: str
    highest_sequence_accepted: int
    highest_sequence_persisted: int
    semantic_count: int
    diagnostic_count: int
    gap_count: int
    dropped_count: int
    suppressed_count: int
    dropped_by_reason: Mapping[str, int]
    redaction_policy_version: str
    completeness: TraceCompleteness
    final_outcome: Mapping[str, Any] | None = None
    last_semantic_activity: str | None = None
    last_observer_heartbeat: str | None = None
    active: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.active, bool):
            raise TypeError("metadata active must be a boolean")

    @property
    def mode(self) -> ObservabilityMode:
        return self.observability_mode

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "root_task_id": self.root_task_id,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "observability_mode": self.observability_mode.value,
            "mode": self.observability_mode.value,
            "status": self.status,
            "active": self.active,
            "highest_sequence_accepted": self.highest_sequence_accepted,
            "highest_sequence_persisted": self.highest_sequence_persisted,
            "semantic_count": self.semantic_count,
            "diagnostic_count": self.diagnostic_count,
            "gap_count": self.gap_count,
            "dropped_count": self.dropped_count,
            "suppressed_count": self.suppressed_count,
            "dropped_by_reason": dict(self.dropped_by_reason),
            "redaction_policy_version": self.redaction_policy_version,
            "completeness": self.completeness.value,
            "final_outcome": dict(self.final_outcome) if self.final_outcome is not None else None,
            "last_semantic_activity": self.last_semantic_activity,
            "last_observer_heartbeat": self.last_observer_heartbeat,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "TraceMetadata":
        if not isinstance(value, Mapping):
            raise TraceCorruptError("trace metadata must be an object")
        try:
            mode = ObservabilityMode.parse(value.get("observability_mode", value.get("mode", "normal")))
            completeness = TraceCompleteness(str(value.get("completeness", value.get("status", "active"))).casefold())
            run_id = value["run_id"]
            root_task_id = value["root_task_id"]
            start_time = value["start_time"]
            if not all(isinstance(item, str) and item for item in (run_id, root_task_id, start_time)):
                raise ValueError("metadata identity/time fields are invalid")
            raw_dropped = value.get("dropped_by_reason", {})
            if not isinstance(raw_dropped, Mapping):
                raise ValueError("metadata dropped_by_reason is invalid")
            dropped_by_reason = {
                str(key): int(item) for key, item in raw_dropped.items() if isinstance(item, int) and item >= 0
            }
            counter_names = (
                "highest_sequence_accepted",
                "highest_sequence_persisted",
                "semantic_count",
                "diagnostic_count",
                "gap_count",
                "dropped_count",
                "suppressed_count",
            )
            counters = {name: value.get(name, 0) for name in counter_names}
            if any(isinstance(item, bool) or not isinstance(item, int) or item < 0 for item in counters.values()):
                raise ValueError("metadata counters are invalid")
            final_outcome = value.get("final_outcome")
            if final_outcome is not None and not isinstance(final_outcome, Mapping):
                raise ValueError("metadata final_outcome is invalid")
            schema_version = value.get("schema_version", TRACE_STORE_SCHEMA_VERSION)
            if isinstance(schema_version, bool) or not isinstance(schema_version, int) or schema_version < 1:
                raise ValueError("metadata schema version is invalid")
            raw_status = value.get("status", completeness.value)
            if not isinstance(raw_status, str) or raw_status.casefold() not in {item.value for item in TraceCompleteness}:
                raise ValueError("metadata status is invalid")
            if raw_status.casefold() != completeness.value:
                raise ValueError("metadata status and completeness conflict")
            raw_active = _active_from_metadata(value, completeness)
            return cls(
                schema_version=schema_version,
                run_id=run_id,
                root_task_id=root_task_id,
                start_time=start_time,
                end_time=value.get("end_time") if isinstance(value.get("end_time"), str) else None,
                observability_mode=mode,
                status=raw_status.casefold(),
                completeness=completeness,
                dropped_by_reason=dropped_by_reason,
                redaction_policy_version=str(value.get("redaction_policy_version", REDACTION_POLICY_VERSION)),
                final_outcome=dict(final_outcome) if final_outcome is not None else None,
                last_semantic_activity=value.get("last_semantic_activity")
                if isinstance(value.get("last_semantic_activity"), str)
                else None,
                last_observer_heartbeat=value.get("last_observer_heartbeat")
                if isinstance(value.get("last_observer_heartbeat"), str)
                else None,
                active=raw_active,
                **counters,
            )
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise TraceCorruptError("invalid trace metadata") from exc


@dataclass(frozen=True, slots=True)
class TraceReadResult:
    records: tuple[ObservationEnvelope, ...]
    completeness: TraceCompleteness
    issues: tuple[str, ...] = ()
    partial_final_line: bool = False
    metadata: TraceMetadata | None = None

    @property
    def corrupt(self) -> bool:
        return self.completeness is TraceCompleteness.CORRUPT

    @property
    def complete(self) -> bool:
        return self.completeness is TraceCompleteness.COMPLETE


@dataclass(frozen=True, slots=True)
class TraceRetentionPolicy:
    """Centralized deterministic retention bounds for owned traces."""

    max_runs: int = 50
    max_bytes: int = 64 * 1024 * 1024
    max_age_seconds: float = 30 * 24 * 60 * 60

    def __post_init__(self) -> None:
        if isinstance(self.max_runs, bool) or not isinstance(self.max_runs, int) or self.max_runs < 1:
            raise ValueError("max_runs must be a positive integer")
        if isinstance(self.max_bytes, bool) or not isinstance(self.max_bytes, int) or self.max_bytes < 1:
            raise ValueError("max_bytes must be a positive integer")
        if isinstance(self.max_age_seconds, bool) or not isinstance(self.max_age_seconds, (int, float)):
            raise TypeError("max_age_seconds must be numeric")
        if self.max_age_seconds < 0:
            raise ValueError("max_age_seconds must be non-negative")


__all__ = [
    "CompletenessStatus",
    "TraceCompleteness",
    "TraceMetadata",
    "TraceReadResult",
    "TraceRetentionPolicy",
    "TraceStatus",
]

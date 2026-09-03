"""Versioned observation envelopes that keep semantic and diagnostic facts distinct."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, cast

from agent.observability.diagnostics import DiagnosticRecord
from agent.observability.envelope_types import (
    INITIAL_OBSERVATION_SEQUENCE,
    TRACE_SCHEMA_VERSION,
    GapMarker,
    ObservationSource,
    _required_text,
    _timestamp,
)
from agent.observability.redaction import (
    canonical_json,
    freeze_observation_value,
    redact_observation_value,
    unfreeze_observation_value,
)
from agent.runtime.events import RuntimeEvent


def _coalesce_payload(
    payload: Mapping[str, Any] | RuntimeEvent | DiagnosticRecord | GapMarker | None,
    record: Mapping[str, Any] | RuntimeEvent | DiagnosticRecord | GapMarker | None,
) -> Any:
    if record is None:
        return payload
    if payload is not None and payload != record:
        raise ValueError("observation payload and record conflict")
    return record


def _project_runtime_payload(
    payload: RuntimeEvent,
    source: ObservationSource,
    run_id: str,
    timestamp: str | datetime | None,
) -> tuple[Mapping[str, Any], str | datetime | None]:
    if source is not ObservationSource.RUNTIME_EVENT:
        raise ValueError("RuntimeEvent payload requires runtime_event source")
    if payload.run_id != run_id:
        raise ValueError("observation run_id conflicts with RuntimeEvent")
    return payload.to_dict(), timestamp if timestamp is not None else payload.timestamp


def _project_diagnostic_payload(
    payload: DiagnosticRecord,
    source: ObservationSource,
    run_id: str,
    timestamp: str | datetime | None,
) -> tuple[Mapping[str, Any], str | datetime | None]:
    if source is not ObservationSource.DIAGNOSTIC:
        raise ValueError("DiagnosticRecord payload requires diagnostic source")
    if payload.run_id is not None and payload.run_id != run_id:
        raise ValueError("observation run_id conflicts with DiagnosticRecord")
    return payload.to_dict(), timestamp if timestamp is not None else payload.timestamp


def _project_gap_payload(
    payload: GapMarker,
    source: ObservationSource,
    timestamp: str | datetime | None,
) -> tuple[Mapping[str, Any], str | datetime | None]:
    if source is not ObservationSource.GAP:
        raise ValueError("GapMarker payload requires gap source")
    return payload.to_dict(), timestamp if timestamp is not None else payload.timestamp


def _project_payload(
    payload: Any,
    source: ObservationSource,
    run_id: str,
    timestamp: str | datetime | None,
) -> tuple[Mapping[str, Any], str | datetime | None]:
    if isinstance(payload, RuntimeEvent):
        return _project_runtime_payload(payload, source, run_id, timestamp)
    if isinstance(payload, DiagnosticRecord):
        return _project_diagnostic_payload(payload, source, run_id, timestamp)
    if isinstance(payload, GapMarker):
        return _project_gap_payload(payload, source, timestamp)
    if isinstance(payload, Mapping):
        projected = redact_observation_value(payload)
        if not isinstance(projected, Mapping):
            raise TypeError("observation payload must be an object")
        return projected, timestamp
    raise TypeError("observation payload must be a mapping or observation record")


def _require_gap_integer(value: Any, name: str, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"gap observation {name} bounds are invalid")
    return cast(int, value)


def _validate_gap_payload(projected: Mapping[str, Any]) -> None:
    start = projected.get("start_sequence")
    end = projected.get("end_sequence")
    dropped = projected.get("dropped_count", 0)
    start_value = _require_gap_integer(start, "start_sequence", INITIAL_OBSERVATION_SEQUENCE)
    _require_gap_integer(end, "end_sequence", start_value)
    _require_gap_integer(dropped, "dropped_count", 0)


def _validate_projected_payload(
    source: ObservationSource,
    projected: Mapping[str, Any],
    run_id: str,
) -> None:
    if source is ObservationSource.RUNTIME_EVENT:
        event_type = projected.get("type")
        if not isinstance(event_type, str) or not event_type.strip():
            raise ValueError("runtime observation payload requires a type")
        payload_run = projected.get("run_id")
        if payload_run is not None and payload_run != run_id:
            raise ValueError("runtime observation payload run_id conflicts with envelope")
        return
    if source is ObservationSource.DIAGNOSTIC:
        if projected.get("type") != "diagnostic":
            raise ValueError("diagnostic observation payload requires diagnostic type")
        if not isinstance(projected.get("kind"), str) or not projected.get("kind"):
            raise ValueError("diagnostic observation payload requires kind")
        return
    if projected.get("type") != "gap":
        raise ValueError("gap observation payload requires gap type")
    _validate_gap_payload(projected)


@dataclass(frozen=True, slots=True, init=False)
class ObservationEnvelope:
    """One ordered, redacted observation accepted by a trace transport."""

    schema_version: int
    source: ObservationSource
    sequence: int
    run_id: str
    timestamp: str
    payload: Mapping[str, Any]

    def __init__(
        self,
        source: ObservationSource | str,
        sequence: int,
        run_id: str,
        payload: Mapping[str, Any] | RuntimeEvent | DiagnosticRecord | GapMarker | None = None,
        *,
        timestamp: str | datetime | None = None,
        schema_version: int = TRACE_SCHEMA_VERSION,
        record: Mapping[str, Any] | RuntimeEvent | DiagnosticRecord | GapMarker | None = None,
    ) -> None:
        payload = _coalesce_payload(payload, record)
        if payload is None:
            raise ValueError("observation payload/record is required")
        if isinstance(schema_version, bool) or not isinstance(schema_version, int) or schema_version < 1:
            raise ValueError("observation schema_version must be positive")
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < INITIAL_OBSERVATION_SEQUENCE:
            raise ValueError("observation sequence must be a positive integer")
        source_value = ObservationSource.parse(source)
        run = _required_text(run_id, "run_id")
        projected, selected_timestamp = _project_payload(payload, source_value, run, timestamp)
        _validate_projected_payload(source_value, projected, run)
        object.__setattr__(self, "schema_version", schema_version)
        object.__setattr__(self, "source", source_value)
        object.__setattr__(self, "sequence", sequence)
        object.__setattr__(self, "run_id", run)
        object.__setattr__(self, "timestamp", _timestamp(selected_timestamp))
        object.__setattr__(self, "payload", freeze_observation_value(projected))

    @classmethod
    def runtime_event(cls, event: RuntimeEvent, sequence: int) -> "ObservationEnvelope":
        return cls(ObservationSource.RUNTIME_EVENT, sequence, event.run_id, event)

    @classmethod
    def diagnostic(cls, record: DiagnosticRecord, sequence: int, *, run_id: str | None = None) -> "ObservationEnvelope":
        selected_run = run_id or record.run_id
        if not selected_run:
            raise ValueError("diagnostic envelope requires run_id")
        return cls(ObservationSource.DIAGNOSTIC, sequence, selected_run, record)

    @classmethod
    def gap(cls, run_id: str, sequence: int, marker: GapMarker) -> "ObservationEnvelope":
        return cls(ObservationSource.GAP, sequence, run_id, marker)

    @property
    def record_type(self) -> str:
        return self.source.value

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source": self.source.value,
            "sequence": self.sequence,
            "run_id": self.run_id,
            "timestamp": self.timestamp,
            "payload": unfreeze_observation_value(self.payload),
        }

    def to_json(self) -> str:
        return canonical_json(self.to_dict())

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ObservationEnvelope":
        if not isinstance(value, Mapping):
            raise TypeError("observation envelope must be an object")
        payload = value.get("payload")
        if not isinstance(payload, Mapping):
            raise ValueError("observation envelope payload must be an object")
        source = value.get("source", value.get("record_type"))
        sequence = value.get("sequence")
        run_id = value.get("run_id")
        if not isinstance(source, (ObservationSource, str)):
            raise ValueError("observation envelope source must be a string")
        if isinstance(sequence, bool) or not isinstance(sequence, int):
            raise ValueError("observation envelope sequence must be an integer")
        if not isinstance(run_id, str) or not run_id:
            raise ValueError("observation envelope run_id must be a non-empty string")
        return cls(
            source,
            sequence,
            run_id,
            payload,
            timestamp=value.get("timestamp"),
            schema_version=value.get("schema_version", TRACE_SCHEMA_VERSION),
        )


ObservationRecord = ObservationEnvelope


__all__ = [
    "GapMarker",
    "INITIAL_OBSERVATION_SEQUENCE",
    "ObservationEnvelope",
    "ObservationRecord",
    "ObservationSource",
    "TRACE_SCHEMA_VERSION",
]

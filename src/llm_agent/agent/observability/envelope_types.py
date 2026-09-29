"""Shared observation source and gap marker value types."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any

TRACE_SCHEMA_VERSION = 1
INITIAL_OBSERVATION_SEQUENCE = 1


class ObservationSource(str, Enum):
    RUNTIME_EVENT = "runtime_event"
    DIAGNOSTIC = "diagnostic"
    GAP = "gap"

    @classmethod
    def parse(cls, value: Any) -> "ObservationSource":
        if isinstance(value, cls):
            return value
        if not isinstance(value, str):
            raise ValueError("observation source is invalid")
        aliases = {
            "runtime": cls.RUNTIME_EVENT,
            "event": cls.RUNTIME_EVENT,
            "runtime_event": cls.RUNTIME_EVENT,
            "semantic": cls.RUNTIME_EVENT,
            "diagnostic": cls.DIAGNOSTIC,
            "gap": cls.GAP,
            "completeness_gap": cls.GAP,
        }
        try:
            return aliases[value.strip().casefold()]
        except KeyError as exc:
            raise ValueError(f"unsupported observation source: {value!r}") from exc


def _required_text(value: Any, name: str, limit: int = 256) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    if len(value) > limit:
        raise ValueError(f"{name} is too long")
    return value


def _timestamp(value: Any) -> str:
    if value is None:
        return datetime.now(timezone.utc).isoformat()
    if isinstance(value, datetime):
        return value.isoformat()
    return _required_text(value, "timestamp", 128)


@dataclass(frozen=True, slots=True)
class GapMarker:
    """Explicit evidence that an observation range is unavailable."""

    reason: str
    start_sequence: int
    end_sequence: int
    dropped_count: int = 0
    timestamp: str = ""

    def __post_init__(self) -> None:
        reason = _required_text(self.reason, "gap reason")
        for name in ("start_sequence", "end_sequence", "dropped_count"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"gap {name} must be an integer")
            if name != "dropped_count" and value < INITIAL_OBSERVATION_SEQUENCE:
                raise ValueError(f"gap {name} must be positive")
            if name == "dropped_count" and value < 0:
                raise ValueError("gap dropped_count must be non-negative")
        if self.end_sequence < self.start_sequence:
            raise ValueError("gap end_sequence must not precede start_sequence")
        object.__setattr__(self, "reason", reason)
        object.__setattr__(self, "timestamp", _timestamp(self.timestamp or None))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": TRACE_SCHEMA_VERSION,
            "type": "gap",
            "reason": self.reason,
            "start_sequence": self.start_sequence,
            "end_sequence": self.end_sequence,
            "dropped_count": self.dropped_count,
            "timestamp": self.timestamp,
        }


__all__ = [
    "GapMarker",
    "INITIAL_OBSERVATION_SEQUENCE",
    "ObservationSource",
    "TRACE_SCHEMA_VERSION",
]

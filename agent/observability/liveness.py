"""Deterministic presentation of persisted trace liveness uncertainty."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from agent.observability.silence import (
    DEFAULT_SILENCE_STALE_AFTER_SECONDS,
    _as_datetime,
)
from agent.observability.trace_types import TraceCompleteness, TraceMetadata


class TraceLivenessState(str, Enum):
    """Presentation states; none of them is a PID/process assertion."""

    CLOSED = "closed"
    LIVE = "live"
    STALE = "stale"


@dataclass(frozen=True, slots=True)
class TraceLiveness:
    """A bounded, explicit projection of an active marker.

    ``LIVE`` means recently observed by a persisted marker/heartbeat.  It is
    intentionally labelled ``recently-live`` in ``certainty`` and never
    claims that a process is definitely alive.  ``STALE`` means that the
    marker is unresolved and must be treated fail-safe by consumers.
    """

    state: TraceLivenessState
    certainty: str
    reason: str
    active_marker: bool
    last_observer_heartbeat: str | None
    heartbeat_age_seconds: float | None
    reference_time: str | None
    reference_age_seconds: float | None
    end_time: str | None
    completeness: str
    as_of: str

    @property
    def definitely_live(self) -> bool:
        """Persisted local facts never prove process liveness."""

        return False

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "certainty": self.certainty,
            "reason": self.reason,
            "active_marker": self.active_marker,
            "definitely_live": self.definitely_live,
            "last_observer_heartbeat": self.last_observer_heartbeat,
            "heartbeat_age_seconds": self.heartbeat_age_seconds,
            "reference_time": self.reference_time,
            "reference_age_seconds": self.reference_age_seconds,
            "end_time": self.end_time,
            "completeness": self.completeness,
            "as_of": self.as_of,
        }


@dataclass(frozen=True, slots=True)
class TraceLivenessPolicy:
    """Centralized threshold for stale active-marker presentation."""

    stale_after_seconds: float = DEFAULT_SILENCE_STALE_AFTER_SECONDS

    def __post_init__(self) -> None:
        if isinstance(self.stale_after_seconds, bool) or not isinstance(
            self.stale_after_seconds, (int, float)
        ):
            raise TypeError("liveness threshold must be numeric")
        if self.stale_after_seconds < 0:
            raise ValueError("liveness threshold must be non-negative")

    @staticmethod
    def _age(reference: str | None, current: datetime) -> float | None:
        if reference is None:
            return None
        try:
            return max(0.0, (current - _as_datetime(reference)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None

    def evaluate(self, metadata: TraceMetadata, now: Any = None) -> TraceLiveness:
        current = _as_datetime(now) if now is not None else datetime.now(timezone.utc)
        as_of = current.isoformat()
        completeness = metadata.completeness.value
        if not metadata.active:
            return TraceLiveness(
                TraceLivenessState.CLOSED,
                "closed-marker",
                "active marker is false",
                False,
                metadata.last_observer_heartbeat,
                self._age(metadata.last_observer_heartbeat, current),
                metadata.end_time,
                self._age(metadata.end_time, current),
                metadata.end_time,
                completeness,
                as_of,
            )

        heartbeat_age = self._age(metadata.last_observer_heartbeat, current)
        reference_time = metadata.last_observer_heartbeat or metadata.start_time
        reference_age = self._age(reference_time, current)
        if metadata.end_time is not None:
            return TraceLiveness(
                TraceLivenessState.STALE,
                "uncertain",
                "active marker conflicts with persisted end_time",
                True,
                metadata.last_observer_heartbeat,
                heartbeat_age,
                reference_time,
                reference_age,
                metadata.end_time,
                completeness,
                as_of,
            )
        if completeness in {
            TraceCompleteness.COMPLETE.value,
            TraceCompleteness.UNCLEAN.value,
            TraceCompleteness.CORRUPT.value,
        }:
            return TraceLiveness(
                TraceLivenessState.STALE,
                "uncertain",
                "active marker has terminal or invalid completeness",
                True,
                metadata.last_observer_heartbeat,
                heartbeat_age,
                reference_time,
                reference_age,
                metadata.end_time,
                completeness,
                as_of,
            )
        if reference_age is None or reference_age >= self.stale_after_seconds:
            return TraceLiveness(
                TraceLivenessState.STALE,
                "uncertain",
                "active marker has no recent persisted observer fact",
                True,
                metadata.last_observer_heartbeat,
                heartbeat_age,
                reference_time,
                reference_age,
                metadata.end_time,
                completeness,
                as_of,
            )
        return TraceLiveness(
            TraceLivenessState.LIVE,
            "recently-live",
            "active marker has a recent persisted observer fact",
            True,
            metadata.last_observer_heartbeat,
            heartbeat_age,
            reference_time,
            reference_age,
            metadata.end_time,
            completeness,
            as_of,
        )


LivenessState = TraceLivenessState


__all__ = [
    "LivenessState",
    "TraceLiveness",
    "TraceLivenessPolicy",
    "TraceLivenessState",
]

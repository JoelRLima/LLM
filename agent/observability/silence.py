"""Deterministic heartbeat and semantic-silence classification."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Mapping

DEFAULT_SILENCE_STALE_AFTER_SECONDS = 120.0


def _as_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        selected = value
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        selected = datetime.fromtimestamp(float(value), tz=timezone.utc)
    elif isinstance(value, str) and value.strip():
        selected = datetime.fromisoformat(value)
    else:
        raise ValueError("clock/timestamp must be datetime, epoch seconds, or ISO text")
    if selected.tzinfo is None:
        selected = selected.replace(tzinfo=timezone.utc)
    return selected.astimezone(timezone.utc)


def parse_timestamp(value: Any) -> datetime:
    """Parse one supported timestamp and normalize it to an absolute instant."""

    return _as_datetime(value)


class SilenceLevel(str, Enum):
    UNKNOWN = "unknown"
    NORMAL = "normal"
    QUIET = "quiet"
    WARNING = "warning"
    STALE = "stale"


@dataclass(frozen=True, slots=True)
class SilencePolicy:
    """Centralized elapsed-silence classification without a hung inference."""

    quiet_after_seconds: float = 5.0
    warning_after_seconds: float = 30.0
    stale_after_seconds: float = DEFAULT_SILENCE_STALE_AFTER_SECONDS

    def __post_init__(self) -> None:
        values = (self.quiet_after_seconds, self.warning_after_seconds, self.stale_after_seconds)
        if any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in values):
            raise TypeError("silence thresholds must be numeric")
        if any(item < 0 for item in values):
            raise ValueError("silence thresholds must be non-negative")
        if not self.quiet_after_seconds <= self.warning_after_seconds <= self.stale_after_seconds:
            raise ValueError("silence thresholds must be ordered")

    def classify(self, elapsed_seconds: float | None) -> SilenceLevel:
        if elapsed_seconds is None:
            return SilenceLevel.UNKNOWN
        if elapsed_seconds >= self.stale_after_seconds:
            return SilenceLevel.STALE
        if elapsed_seconds >= self.warning_after_seconds:
            return SilenceLevel.WARNING
        if elapsed_seconds >= self.quiet_after_seconds:
            return SilenceLevel.QUIET
        return SilenceLevel.NORMAL

    def evaluate(
        self,
        *,
        last_semantic_activity: str | None,
        last_observer_heartbeat: str | None,
        now: Any = None,
        canonical_watchdog: str | None = None,
        active_context: Mapping[str, Any] | None = None,
    ) -> "SilenceStatus":
        current = _as_datetime(now) if now is not None else datetime.now(timezone.utc)
        elapsed: float | None = None
        if last_semantic_activity:
            try:
                elapsed = max(0.0, (current - _as_datetime(last_semantic_activity)).total_seconds())
            except (TypeError, ValueError, OverflowError):
                elapsed = None
        heartbeat_age: float | None = None
        if last_observer_heartbeat:
            try:
                heartbeat_age = max(0.0, (current - _as_datetime(last_observer_heartbeat)).total_seconds())
            except (TypeError, ValueError, OverflowError):
                heartbeat_age = None
        return SilenceStatus(
            level=self.classify(elapsed),
            last_semantic_activity=last_semantic_activity,
            last_observer_heartbeat=last_observer_heartbeat,
            elapsed_seconds=elapsed,
            heartbeat_age_seconds=heartbeat_age,
            canonical_watchdog=canonical_watchdog,
            active_context=active_context or {},
            as_of=current.isoformat(),
        )


@dataclass(frozen=True, slots=True)
class SilenceStatus:
    """Separate observer heartbeat and semantic silence facts."""

    level: SilenceLevel
    last_semantic_activity: str | None
    last_observer_heartbeat: str | None
    elapsed_seconds: float | None
    heartbeat_age_seconds: float | None
    canonical_watchdog: str | None
    active_context: Mapping[str, Any]
    as_of: str

    @property
    def silence(self) -> str:
        return self.level.value

    def to_dict(self) -> dict[str, Any]:
        return {
            "silence": self.level.value,
            "last_semantic_activity": self.last_semantic_activity,
            "observer_heartbeat": self.last_observer_heartbeat,
            "semantic_activity": self.last_semantic_activity,
            "elapsed_seconds": self.elapsed_seconds,
            "heartbeat_age_seconds": self.heartbeat_age_seconds,
            "canonical_watchdog": self.canonical_watchdog,
            "active_context": dict(self.active_context),
            "as_of": self.as_of,
        }


def clock_now(clock: Callable[[], Any] | None) -> datetime:
    return _as_datetime(clock() if clock is not None else datetime.now(timezone.utc))


__all__ = [
    "DEFAULT_SILENCE_STALE_AFTER_SECONDS",
    "parse_timestamp",
    "SilenceLevel",
    "SilencePolicy",
    "SilenceStatus",
    "clock_now",
]

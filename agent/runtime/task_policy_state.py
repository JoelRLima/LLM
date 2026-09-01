"""Atomic checkpointable state owned by the task policy seam."""

from __future__ import annotations

import math
import time
from collections.abc import Mapping
from contextlib import contextmanager
from threading import RLock
from typing import Any, Iterator


class TaskPolicyState:
    """Own logical admissions and cumulative active duration only."""

    def __init__(self) -> None:
        self._logical_work_units_consumed = 0
        self._active_elapsed_seconds = 0.0
        self._active_started_at: float | None = None
        self._lock = RLock()

    @property
    def logical_work_units_consumed(self) -> int:
        with self._lock:
            return self._logical_work_units_consumed

    @property
    def active_elapsed_seconds(self) -> float:
        return self.active_elapsed_at()

    def reset(self) -> None:
        with self._lock:
            self._logical_work_units_consumed = 0
            self._active_elapsed_seconds = 0.0
            self._active_started_at = None

    def start_active_segment(self, now: float | None = None) -> None:
        selected = time.monotonic() if now is None else _clock_value(now)
        with self._lock:
            if self._active_started_at is None:
                self._active_started_at = selected

    def pause_active_segment(self, now: float | None = None) -> float:
        selected = time.monotonic() if now is None else _clock_value(now)
        with self._lock:
            self._accumulate_until(selected)
            self._active_started_at = None
            return self._active_elapsed_seconds

    def active_elapsed_at(self, now: float | None = None) -> float:
        selected = time.monotonic() if now is None else _clock_value(now)
        with self._lock:
            elapsed = self._active_elapsed_seconds
            if self._active_started_at is not None:
                elapsed += max(0.0, selected - self._active_started_at)
            return elapsed

    def consume_logical_work_units(self, amount: int, maximum: int) -> int:
        _positive_int(amount, "amount")
        _non_negative_int(maximum, "maximum")
        with self._lock:
            remaining = max(0, maximum - self._logical_work_units_consumed)
            admitted = min(amount, remaining)
            self._logical_work_units_consumed += admitted
            return admitted

    def remaining_logical_work_units(self, maximum: int) -> int:
        _non_negative_int(maximum, "maximum")
        with self._lock:
            return max(0, maximum - self._logical_work_units_consumed)

    def to_checkpoint_dict(self, now: float | None = None) -> dict[str, Any]:
        return {
            "logical_work_units_consumed": self.logical_work_units_consumed,
            "active_elapsed_seconds": round(self.active_elapsed_at(now), 6),
        }

    def restore_checkpoint(self, raw: Mapping[str, Any], *, maximum: int | None = None) -> None:
        if not isinstance(raw, Mapping):
            raise ValueError("task policy checkpoint must be an object")
        retired = sorted(
            key
            for key in ("consumed_logical_steps", "active_elapsed")
            if key in raw
        )
        if retired:
            raise ValueError(
                "W7_RETIRED_TASK_POLICY_KEY:" + ",".join(retired)
            )
        required = ("logical_work_units_consumed", "active_elapsed_seconds")
        if any(key not in raw for key in required):
            raise ValueError("W7_CANONICAL_TASK_POLICY_KEYS_REQUIRED")
        consumed = raw["logical_work_units_consumed"]
        elapsed = raw["active_elapsed_seconds"]
        _non_negative_int(consumed, "logical_work_units_consumed")
        elapsed = _finite_non_negative(elapsed, "active_elapsed_seconds")
        if maximum is not None and consumed > maximum:
            raise ValueError("task policy checkpoint exceeds max_steps")
        with self._lock:
            self._logical_work_units_consumed = consumed
            self._active_elapsed_seconds = elapsed
            self._active_started_at = None

    @contextmanager
    def atomic(self) -> Iterator[None]:
        with self._lock:
            yield

    def __getstate__(self) -> dict[str, Any]:
        return self.to_checkpoint_dict()

    def __setstate__(self, state: Mapping[str, Any]) -> None:
        self._lock = RLock()
        self._logical_work_units_consumed = 0
        self._active_elapsed_seconds = 0.0
        self._active_started_at = None
        self.restore_checkpoint(state)

    def _accumulate_until(self, now: float) -> None:
        if self._active_started_at is not None:
            self._active_elapsed_seconds += max(0.0, now - self._active_started_at)


def _clock_value(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("clock value must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("clock value must be finite")
    return result


def _finite_non_negative(value: Any, name: str) -> float:
    result = _clock_value(value)
    if result < 0:
        raise ValueError(f"{name} must be non-negative")
    return result


def _non_negative_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _positive_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


__all__ = ["TaskPolicyState"]

"""Neutral operation-level value contracts."""

from __future__ import annotations

from enum import Enum


class CancellationSafetyMode(str, Enum):
    """How an adapter closes its lifetime after timeout/cancellation."""

    BOUNDED_COOPERATIVE = "bounded_cooperative"
    PROCESS_KILLABLE = "process_killable"
    UNSUPPORTED = "unsupported"


__all__ = ["CancellationSafetyMode"]

"""Clock normalization for feedback records."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

_ErrorFactory = Callable[[str, str], Exception]


def default_feedback_clock() -> str:
    return datetime.now(timezone.utc).isoformat()


def resolve_feedback_time(value: Any, *, invalid: _ErrorFactory) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    if not isinstance(value, str) or not value.strip():
        raise invalid("FEEDBACK_TARGET_INVALID", "created_at is required")
    return value


__all__ = ["default_feedback_clock", "resolve_feedback_time"]

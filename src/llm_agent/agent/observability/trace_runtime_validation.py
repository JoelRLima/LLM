"""Constructor validation for the bounded trace runtime."""

from __future__ import annotations

import math


def validate_constructor_options(
    root_task_id: str,
    queue_capacity: int,
    shutdown_timeout_seconds: float,
    metadata_coalesce_delay_seconds: float,
) -> None:
    if isinstance(queue_capacity, bool) or not isinstance(queue_capacity, int) or queue_capacity < 1:
        raise ValueError("queue_capacity must be a positive integer")
    if shutdown_timeout_seconds < 0:
        raise ValueError("shutdown_timeout_seconds must be non-negative")
    if (
        isinstance(metadata_coalesce_delay_seconds, bool)
        or not isinstance(metadata_coalesce_delay_seconds, (int, float))
        or not math.isfinite(metadata_coalesce_delay_seconds)
        or metadata_coalesce_delay_seconds < 0
    ):
        raise ValueError("metadata_coalesce_delay_seconds must be a finite non-negative number")
    if not isinstance(root_task_id, str) or not root_task_id.strip():
        raise ValueError("root_task_id must be a non-empty string")

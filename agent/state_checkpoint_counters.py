"""Validation and restoration of checkpoint counters."""

from __future__ import annotations

from typing import Any


def _non_negative_counter(value: Any, message: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(message)
    return value


def restore_counters(state: Any, data: dict[str, Any]) -> None:
    continuation_attempts = _non_negative_counter(
        data.get("continuation_attempts", 0),
        "Checkpoint continuation counter is invalid.",
    )
    reasoning_turns_used = _non_negative_counter(
        data.get("reasoning_turns_used", 0),
        "Checkpoint reasoning counter is invalid.",
    )
    state.continuation_attempts = continuation_attempts
    state.replan_counts = _restore_replan_counts(
        data.get("replan_counts", getattr(state, "replan_counts", {}))
    )
    state.reasoning_turns_used = reasoning_turns_used
    state.reasoning_last_history_count = _restore_history_cursor(state, data)
    state.reasoning_last_progress_token = _restore_progress_token(data)
    state.continue_after_plan = _restore_continuation_flag(data)


def _restore_replan_counts(raw_replans: Any) -> dict[str, int]:
    if not isinstance(raw_replans, dict):
        raise ValueError("Checkpoint replan counters are invalid.")
    replans: dict[str, int] = {}
    for key in ("total", "heuristic", "llm"):
        replans[key] = _non_negative_counter(
            raw_replans.get(key, 0),
            "Checkpoint replan counters are invalid.",
        )
    if replans["total"] < replans["heuristic"] + replans["llm"]:
        raise ValueError("Checkpoint replan total is inconsistent.")
    return replans


def _restore_history_cursor(state: Any, data: dict[str, Any]) -> int:
    raw_cursor = data.get("reasoning_last_history_count")
    if raw_cursor is None:
        raw_cursor = len(data.get("tool_history") or [])
    if isinstance(raw_cursor, bool) or not isinstance(raw_cursor, int) or raw_cursor < -1:
        raise ValueError("Checkpoint reasoning history cursor is invalid.")
    return raw_cursor


def _restore_progress_token(data: dict[str, Any]) -> str | None:
    token = data.get("reasoning_last_progress_token")
    if token is not None and not isinstance(token, str):
        raise ValueError("Checkpoint reasoning progress token is invalid.")
    return token


def _restore_continuation_flag(data: dict[str, Any]) -> bool:
    continue_after_plan = data.get("continue_after_plan", False)
    if not isinstance(continue_after_plan, bool):
        raise ValueError("Checkpoint continuation flag is invalid.")
    return continue_after_plan


__all__ = ["restore_counters"]

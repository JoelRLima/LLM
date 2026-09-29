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
    replan_counts = _restore_replan_counts(
        data.get("replan_counts", {"total": 0, "heuristic": 0, "llm": 0})
    )
    recovery = getattr(state, "recovery_budget", None)
    raw_recovery = data.get("recovery_budget")
    if recovery is not None:
        if raw_recovery is not None:
            _validate_canonical_legacy_conflicts(
                raw_recovery,
                data,
                continuation_attempts,
                replan_counts,
                reasoning_turns_used,
            )
            recovery.restore_snapshot(raw_recovery)
        else:
            recovery.restore_legacy_projection(
                continuation_attempts=continuation_attempts,
                replan_counts=replan_counts,
                reasoning_turns_used=reasoning_turns_used,
            )
    else:
        state.continuation_attempts = continuation_attempts
        state.replan_counts = replan_counts
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
    if replans["total"] != replans["heuristic"] + replans["llm"]:
        raise ValueError("Checkpoint replan total is inconsistent.")
    return replans


def _validate_canonical_legacy_conflicts(
    raw_recovery: Any,
    data: dict[str, Any],
    continuation_attempts: int,
    replan_counts: dict[str, int],
    reasoning_turns_used: int,
) -> None:
    if not isinstance(raw_recovery, dict):
        raise ValueError("Checkpoint recovery budget is invalid.")
    raw_used = raw_recovery.get("used")
    if not isinstance(raw_used, dict):
        raise ValueError("Checkpoint recovery budget state is invalid.")

    def canonical(name: str) -> int:
        return _non_negative_counter(
            raw_used.get(name, 0),
            "Checkpoint recovery budget state is invalid.",
        )

    if "continuation_attempts" in data and canonical("effect_continuations") != continuation_attempts:
        raise ValueError("Checkpoint recovery budget conflicts with continuation counter.")
    if "reasoning_turns_used" in data and canonical("reasoning_continuations") != reasoning_turns_used:
        raise ValueError("Checkpoint recovery budget conflicts with reasoning counter.")
    if "replan_counts" in data:
        if canonical("heuristic_replans") != replan_counts["heuristic"] or canonical("llm_replans") != replan_counts["llm"]:
            raise ValueError("Checkpoint recovery budget conflicts with replan counters.")
        if replan_counts["total"] != replan_counts["heuristic"] + replan_counts["llm"]:
            raise ValueError("Checkpoint generic replan total is not a canonical projection.")


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

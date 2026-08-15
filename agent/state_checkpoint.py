"""Checkpoint serialization for task-level progression fields."""

from __future__ import annotations

from typing import Any


def progression_checkpoint(state: Any) -> dict[str, Any]:
    return {
        "requested_effects": state.requested_effects,
        "executed_effects": state.executed_effects,
        "waived_effects": state.waived_effects,
        "continuation_attempts": state.continuation_attempts,
        "terminal_disposition": state.terminal_disposition,
    }


def restore_progression(state: Any, data: dict[str, Any]) -> None:
    state.requested_effects = [str(item) for item in (data.get("requested_effects") or [])]
    state.executed_effects = [str(item) for item in (data.get("executed_effects") or [])]
    state.waived_effects = [str(item) for item in (data.get("waived_effects") or [])]
    state.continuation_attempts = int(data.get("continuation_attempts") or 0)
    terminal = data.get("terminal_disposition")
    state.terminal_disposition = str(terminal) if terminal is not None else None

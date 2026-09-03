"""Checkpoint serialization for task-level progression fields."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from agent.planning.task_completion_types import CompletionDisposition
from agent.planning.task_semantics import TaskSemantics, TaskSemanticsError
from agent.runtime.outcome_taxonomy import NON_SUCCESS_STATUSES
from agent.state_checkpoint_counters import restore_counters as _restore_counters

_VALID_TERMINAL_DISPOSITIONS = (
    frozenset(item.value for item in CompletionDisposition) | NON_SUCCESS_STATUSES
)
CONTINUITY_SCHEMA_VERSION = 1
CONTINUITY_MAX_STRING_LENGTH = 512
CONTINUITY_MAX_TIMESTAMP_LENGTH = 128
_CONTINUITY_FIELDS = frozenset(
    {
        "schema_version",
        "resume_generation",
        "last_run_id",
        "resumed_from_run_id",
        "interrupted",
        "interruption_reason",
        "interrupted_at",
    }
)


def progression_checkpoint(state: Any) -> dict[str, Any]:
    semantics = getattr(state, "task_semantics", None)
    objective = getattr(state, "objective", None)
    semantic_checkpoint = (
        semantics.to_checkpoint_dict()
        if isinstance(semantics, TaskSemantics)
        and (
            objective == semantics.objective
            or (objective is None and semantics.objective == "")
        )
        else None
    )
    checkpoint = {
        "requested_effects": state.requested_effects,
        "executed_effects": state.executed_effects,
        "waived_effects": state.waived_effects,
        "prohibited_effects": getattr(state, "prohibited_effects", []),
        "task_semantics": semantic_checkpoint,
        "continuation_attempts": state.continuation_attempts,
        "replan_counts": dict(getattr(state, "replan_counts", {}) or {}),
        "reasoning_turns_used": state.reasoning_turns_used,
        "recovery_budget": (
            state.recovery_budget.to_checkpoint_dict()
            if callable(getattr(getattr(state, "recovery_budget", None), "to_checkpoint_dict", None))
            else None
        ),
        "reasoning_last_history_count": state.reasoning_last_history_count,
        "reasoning_last_progress_token": state.reasoning_last_progress_token,
        "continue_after_plan": state.continue_after_plan,
        "terminal_disposition": state.terminal_disposition,
        "task_policy": (
            state.task_policy_state.to_checkpoint_dict()
            if callable(getattr(getattr(state, "task_policy_state", None), "to_checkpoint_dict", None))
            else None
        ),
        "hierarchical_lifecycle": dict(
            getattr(state, "hierarchical_lifecycle", {"status": "inactive"})
        ),
    }
    continuity = continuity_checkpoint(state)
    if continuity is not None:
        checkpoint["continuity"] = continuity
    return checkpoint


def restore_progression(state: Any, data: dict[str, Any]) -> None:
    _restore_continuity(state, data)
    _restore_semantics(state, data)
    _restore_counters(state, data)
    _restore_task_policy(state, data)
    _restore_hierarchical_lifecycle(state, data)
    _restore_terminal(state, data)


def continuity_checkpoint(state: Any) -> dict[str, Any] | None:
    raw = getattr(state, "continuity", None)
    if raw is None:
        return None
    return validate_continuity_metadata(raw)


def validate_continuity_metadata(raw: Any) -> dict[str, Any]:
    """Validate and project the bounded schema-1 continuity object.

    The top-level checkpoint remains schema 2.  This nested object is optional
    so schema-2 checkpoints written before continuity existed remain valid.
    """

    if not isinstance(raw, Mapping):
        raise ValueError("Checkpoint continuity metadata is invalid.")
    keys = tuple(raw.keys())
    if any(not isinstance(key, str) for key in keys):
        raise ValueError("Checkpoint continuity metadata keys are invalid.")
    unexpected = sorted(set(keys) - _CONTINUITY_FIELDS)
    if unexpected:
        raise ValueError(
            "Checkpoint continuity metadata contains unsupported fields: "
            + ",".join(unexpected)
        )

    schema_version = raw.get("schema_version")
    if isinstance(schema_version, bool) or schema_version != CONTINUITY_SCHEMA_VERSION:
        raise ValueError("Checkpoint continuity metadata schema is unsupported.")
    generation = raw.get("resume_generation")
    if isinstance(generation, bool) or not isinstance(generation, int) or generation < 0:
        raise ValueError("Checkpoint continuity resume generation is invalid.")
    if "last_run_id" not in raw:
        raise ValueError("Checkpoint continuity metadata lacks last_run_id.")
    last_run_id = _bounded_continuity_text(raw.get("last_run_id"), "last_run_id")
    if "interrupted" not in raw or not isinstance(raw.get("interrupted"), bool):
        raise ValueError("Checkpoint continuity interruption flag is invalid.")
    interrupted = bool(raw["interrupted"])
    interruption_reason = _bounded_continuity_text(
        raw.get("interruption_reason"), "interruption_reason"
    )
    interrupted_at = _bounded_timestamp(raw.get("interrupted_at"))
    if not interrupted and (interruption_reason is not None or interrupted_at is not None):
        raise ValueError(
            "Checkpoint continuity metadata contradicts interrupted=false."
        )
    resumed_from_run_id = _bounded_continuity_text(
        raw.get("resumed_from_run_id"), "resumed_from_run_id"
    )
    result: dict[str, Any] = {
        "schema_version": CONTINUITY_SCHEMA_VERSION,
        "resume_generation": generation,
        "last_run_id": last_run_id,
        "interrupted": interrupted,
        "interruption_reason": interruption_reason,
        "interrupted_at": interrupted_at,
    }
    if "resumed_from_run_id" in raw:
        result["resumed_from_run_id"] = resumed_from_run_id
    return result


def _restore_continuity(state: Any, data: Mapping[str, Any]) -> None:
    raw = data.get("continuity")
    if raw is None:
        state.continuity = None
        state._continuity_bound_run_id = None
        state._continuity_resume_pending = False
        return
    normalized = validate_continuity_metadata(raw)
    state.continuity = normalized
    state._continuity_bound_run_id = normalized.get("last_run_id")
    state._continuity_resume_pending = False


def _bounded_continuity_text(value: Any, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Checkpoint continuity {name} is invalid.")
    if len(value) > CONTINUITY_MAX_STRING_LENGTH:
        raise ValueError(f"Checkpoint continuity {name} exceeds its bound.")
    return value


def _bounded_timestamp(value: Any) -> str | None:
    text = _bounded_continuity_text(value, "interrupted_at")
    if text is None:
        return None
    if len(text) > CONTINUITY_MAX_TIMESTAMP_LENGTH:
        raise ValueError("Checkpoint continuity interrupted_at exceeds its bound.")
    candidate = text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text
    try:
        datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise ValueError("Checkpoint continuity interrupted_at is invalid.") from exc
    return text


def _restore_task_policy(state: Any, data: dict[str, Any]) -> None:
    policy_state = getattr(state, "task_policy_state", None)
    if policy_state is None:
        return
    raw = data.get("task_policy")
    if raw is None:
        policy_state.reset()
        return
    if not isinstance(raw, dict):
        raise ValueError("Checkpoint task policy state is invalid.")
    try:
        policy_state.restore_checkpoint(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("Checkpoint task policy state is invalid.") from exc


def _restore_hierarchical_lifecycle(state: Any, data: dict[str, Any]) -> None:
    raw = data.get("hierarchical_lifecycle", {"status": "inactive"})
    if not isinstance(raw, dict):
        raise ValueError("Checkpoint hierarchical lifecycle is invalid.")
    status = raw.get("status", "inactive")
    if status not in {"inactive", "running", "completed"}:
        raise ValueError("Checkpoint hierarchical lifecycle status is invalid.")
    state.hierarchical_lifecycle = dict(raw)


def _restore_legacy_semantics(state: Any, data: dict[str, Any]) -> None:
    legacy_keys = ("requested_effects", "executed_effects", "waived_effects")
    if any(key not in data for key in legacy_keys):
        raise ValueError(
            "Checkpoint lacks canonical task semantics and has no unambiguous legacy state."
        )
    for key in legacy_keys + ("prohibited_effects",):
        if key in data and not _string_list(data[key]):
            raise ValueError(f"Checkpoint field is not a string list: {key}.")
    state.set_task_semantics(
        TaskSemantics.from_legacy(
            str(getattr(state, "objective", None) or ""),
            data["requested_effects"],
            data["executed_effects"],
            data["waived_effects"],
            data.get("prohibited_effects") or [],
        )
    )


def _restore_semantics(state: Any, data: dict[str, Any]) -> None:
    raw_semantics = data.get("task_semantics")
    if isinstance(raw_semantics, dict) and hasattr(state, "set_task_semantics"):
        try:
            semantics = TaskSemantics.from_checkpoint_dict(raw_semantics)
        except (TaskSemanticsError, TypeError, AttributeError) as exc:
            raise ValueError("Checkpoint contains invalid task semantics.") from exc
        objective = getattr(state, "objective", None)
        if objective is not None and semantics.objective != objective:
            raise ValueError("Checkpoint task intent does not match its objective.")
        state.set_task_semantics(semantics)
    elif hasattr(state, "set_task_semantics"):
        _restore_legacy_semantics(state, data)
    else:
        state.requested_effects = [str(item) for item in (data.get("requested_effects") or [])]
        state.executed_effects = [str(item) for item in (data.get("executed_effects") or [])]
        state.waived_effects = [str(item) for item in (data.get("waived_effects") or [])]


def _restore_terminal(state: Any, data: dict[str, Any]) -> None:
    terminal = data.get("terminal_disposition")
    if terminal is None:
        setter = getattr(state, "set_terminal_disposition", None)
        if callable(setter):
            setter(None)
        else:
            state.terminal_disposition = None
        return
    if not isinstance(terminal, str):
        raise ValueError("Checkpoint terminal disposition is invalid.")
    normalized_terminal = terminal
    if normalized_terminal not in _VALID_TERMINAL_DISPOSITIONS:
        raise ValueError("Checkpoint contains an unsupported terminal disposition.")
    setter = getattr(state, "set_terminal_disposition", None)
    if callable(setter):
        setter(normalized_terminal)
    else:
        state.terminal_disposition = normalized_terminal


def _string_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)

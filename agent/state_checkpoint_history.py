"""Canonical tool-history and task-semantics checkpoint restoration."""

from __future__ import annotations

from typing import Any, Mapping

from agent.planning.task_semantics import TaskSemantics, TaskSemanticsError
from agent.planning.task_semantics_effects import (
    effect_observation_proves_terminal,
    observed_effect_kinds,
)
from agent.planning.task_semantics_restore import revalidate_restored_terminal_evidence
from agent.planning.task_semantics_types import ObligationStatus


def _validated_tool_history(state: Any, data: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw_history = data.get("tool_history", state.tool_history) or []
    if not isinstance(raw_history, list) or any(not isinstance(entry, Mapping) for entry in raw_history):
        raise ValueError("Checkpoint tool history is invalid.")
    return [dict(entry) for entry in raw_history]


def _restore_history_plan_ids(state: Any) -> None:
    plan_ids = {str(step.get("_step_id")) for step in state.plan if step.get("_step_id")}
    for entry in state.tool_history:
        if state.plan_identity is not None and "plan_id" not in entry and entry.get("step_id") in plan_ids:
            entry["plan_id"] = state.plan_identity


def _register_history_observations(state: Any) -> None:
    semantics = getattr(state, "task_semantics", None)
    register = getattr(semantics, "register_observation", None)
    if callable(register):
        for index, entry in enumerate(state.tool_history, start=1):
            result = entry.get("result")
            if isinstance(result, Mapping):
                register(
                    str(entry.get("tool", "")),
                    result,
                    evidence_ref=index,
                    args=entry.get("args") if isinstance(entry.get("args"), Mapping) else {},
                )


def _rebuild_legacy_semantics(
    semantics: TaskSemantics,
    history: list[dict[str, Any]],
    *,
    effect_authority: Any = None,
) -> None:
    """Derive legacy terminal facts from history, never from legacy lists."""

    for index, entry in enumerate(history, start=1):
        result = entry.get("result")
        if not isinstance(result, Mapping):
            continue
        semantics.observe_tool(
            str(entry.get("tool", "")),
            result,
            evidence_ref=index,
            args=entry.get("args") if isinstance(entry.get("args"), Mapping) else {},
        )
    if effect_authority is None:
        return
    for index, entry in enumerate(history, start=1):
        if not effect_observation_proves_terminal(
            effect_authority,
            ObligationStatus.SATISFIED,
            entry,
        ):
            continue
        semantics.record_effect(
            "write",
            evidence_ref=index,
            effect_authority=effect_authority,
        )


def _reconstruct_modern_unbound_effects(
    semantics: TaskSemantics,
    history: list[dict[str, Any]],
    *,
    legacy_semantics: bool,
    effect_authority: Any = None,
) -> None:
    """Rebuild unbound operational effects from canonical modern history."""

    if legacy_semantics or effect_authority is None:
        return
    catalog = getattr(semantics, "_evidence_catalog", {})
    for index, _entry in enumerate(history, start=1):
        observation = catalog.get(index)
        if not isinstance(observation, Mapping):
            continue
        for effect in observed_effect_kinds(effect_authority, observation):
            semantics.record_effect(
                effect,
                evidence_ref=index,
                effect_authority=effect_authority,
            )
            if effect not in semantics.requested_effects:
                semantics.record_unrequested_effect(
                    effect,
                    evidence_ref=index,
                    effect_authority=effect_authority,
                )


def _restore_history_semantics(
    state: Any,
    data: Mapping[str, Any],
    semantics: TaskSemantics,
    *,
    effect_authority: Any = None,
) -> None:
    legacy_semantics = not isinstance(data.get("task_semantics"), Mapping)
    try:
        if legacy_semantics:
            _rebuild_legacy_semantics(
                semantics,
                state.tool_history,
                effect_authority=effect_authority,
            )
        revalidate_restored_terminal_evidence(
            semantics,
            effect_authority=effect_authority,
        )
        _reconstruct_modern_unbound_effects(
            semantics,
            state.tool_history,
            legacy_semantics=legacy_semantics,
            effect_authority=effect_authority,
        )
    except (TaskSemanticsError, TypeError, AttributeError) as exc:
        raise ValueError(
            "Checkpoint task semantics evidence does not match canonical history."
        ) from exc


def restore_histories(
    state: Any,
    data: Mapping[str, Any],
    *,
    effect_authority: Any = None,
) -> None:
    state.tool_history = _validated_tool_history(state, data)
    _restore_history_plan_ids(state)
    _register_history_observations(state)
    semantics = getattr(state, "task_semantics", None)
    if not isinstance(semantics, TaskSemantics):
        raise ValueError("Checkpoint task semantics owner is invalid after restore.")
    validate_admission = getattr(semantics, "validate_admission_provenance", None)
    if callable(validate_admission):
        validate_admission()
    _restore_history_semantics(
        state,
        data,
        semantics,
        effect_authority=effect_authority,
    )


__all__ = ["restore_histories"]

"""Canonical observation/effect projections used by task completion."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent.execution_state import StepStatus
from agent.planning.completion_observations_support import (
    eligible_waiver_observations,
    observation_references,
)
from agent.planning.effect_intent import effect_intent_matches
from agent.planning.task_semantics_effects import observed_effect_accesses, observed_effect_kinds
from agent.planning.task_semantics_effects import (
    tool_capabilities as _tool_capabilities,
)
from agent.resources.contracts import WORKSPACE_RESOURCE, ResourceAccess, ResourceMode, ResourceProvenance
from agent.runtime.failure_policy import (
    FailureClass,
    classify_failure,
    local_failure_permitted,
    unrecovered_local_failure_observations,
)
from agent.runtime.operational_outcome import project_operational_outcome
from agent.runtime.outcome_taxonomy import NON_SUCCESS_STATUSES

tool_capabilities = _tool_capabilities

__all__ = [
    "eligible_waiver_observations",
    "observation_references",
    "publish_outcome",
    "refresh_executed_effects",
    "terminal_failure",
]


def refresh_executed_effects(orchestrator: Any) -> None:
    state = orchestrator.agent_state
    for history_index, item in enumerate(getattr(state, "tool_history", ()) or (), start=1):
        _refresh_observation(orchestrator, history_index, item)


def _refresh_observation(orchestrator: Any, history_index: int, item: Any) -> None:
    if not isinstance(item, Mapping) or not isinstance(item.get("result"), Mapping):
        return
    observed = observed_effect_accesses(orchestrator, item)
    persisted_effects = set(observed_effect_kinds(orchestrator, item))
    effects = tuple(dict.fromkeys(effect for effect, _access in observed))
    if not effects:
        effects = observed_effect_kinds(orchestrator, item)
    if not effects:
        return
    semantics = getattr(orchestrator.agent_state, "task_semantics", None)
    _register_observation(semantics, item, history_index)
    observed = observed or _fallback_observations(effects)
    requested, prohibited = _split_effect_intents(semantics)
    _record_observed_effects(
        orchestrator,
        history_index,
        observed,
        persisted_effects,
        requested,
        prohibited,
        semantics,
    )


def _register_observation(semantics: Any, item: Mapping[str, Any], history_index: int) -> None:
    register = getattr(semantics, "register_observation", None)
    if callable(register):
        register(
            str(item.get("tool", "")),
            item["result"],
            evidence_ref=history_index,
            args=item.get("args") if isinstance(item.get("args"), dict) else {},
        )


def _fallback_observations(
    effects: tuple[str, ...],
) -> tuple[tuple[str, ResourceAccess], ...]:
    return tuple(
        (
            effect,
            ResourceAccess(
                WORKSPACE_RESOURCE,
                ResourceMode.WRITE,
                ResourceProvenance.OBSERVED_MUTATION,
            ),
        )
        for effect in effects
    )


def _split_effect_intents(semantics: Any) -> tuple[tuple[Any, ...], tuple[Any, ...]]:
    intents = tuple(getattr(semantics, "effect_intents", ()) if semantics is not None else ())
    return (
        tuple(item for item in intents if not getattr(item, "prohibited", False)),
        tuple(item for item in intents if getattr(item, "prohibited", False)),
    )


def _record_observed_effects(
    orchestrator: Any,
    history_index: int,
    observed: tuple[tuple[str, ResourceAccess], ...],
    persisted_effects: set[str],
    requested: tuple[Any, ...],
    prohibited: tuple[Any, ...],
    semantics: Any,
) -> None:
    state = orchestrator.agent_state
    requested_effects = tuple(getattr(state, "requested_effects", ()) or ())
    predicate_resolutions = (
        getattr(semantics, "predicate_resolutions", None)
        if semantics is not None
        else None
    )
    seen_pairs: set[tuple[str, str]] = set()
    for effect, access in observed:
        pair = (effect, access.name)
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)
        is_requested = any(
            effect_intent_matches(
                intent,
                effect,
                access,
                predicate_resolutions=predicate_resolutions,
            )
            for intent in requested
        )
        is_prohibited = any(
            effect_intent_matches(
                intent,
                effect,
                access,
                predicate_resolutions=predicate_resolutions,
            )
            for intent in prohibited
        )
        if effect in persisted_effects and (is_requested or effect not in requested_effects):
            state.record_executed_effect(
                effect,
                evidence_ref=history_index,
                effect_authority=orchestrator,
            )
        _record_effect_violation(semantics, effect, history_index, is_prohibited, is_requested, orchestrator)


def _record_effect_violation(
    semantics: Any,
    effect: str,
    history_index: int,
    prohibited: bool,
    requested: bool,
    authority: Any,
) -> None:
    if prohibited:
        record = getattr(semantics, "record_prohibited_effect", None)
        if callable(record):
            record(effect, evidence_ref=history_index, effect_authority=authority)
    if not requested:
        record = getattr(semantics, "record_unrequested_effect", None)
        if callable(record):
            record(effect, evidence_ref=history_index, effect_authority=authority, force=True)


def publish_outcome(orchestrator: Any) -> None:
    projection = project_operational_outcome(
        orchestrator.agent_state,
        task_failed=bool(getattr(orchestrator, "_task_failed", False)),
        cancelled=bool(getattr(orchestrator, "_cancelled", False)),
    ).debug_projection()
    events = getattr(orchestrator.agent_state, "events", None) or ()
    emit = getattr(orchestrator, "_emit", None)
    if not callable(emit):
        return
    if any(
        isinstance(event, dict)
        and event.get("type") == "task_outcome"
        and event.get("data") == projection
        for event in events
    ):
        return
    emit("task_outcome", projection)


def _later_recovery(state: Any, index: int, entry: Mapping[str, Any]) -> bool:
    checker = getattr(state, "_later_recovery", None)
    return bool(checker(index, entry)) if callable(checker) else False


def _step_failure_requires_terminal(state: Any, record: Any, permits: Any) -> bool:
    relevant = [
        (index, entry)
        for index, entry in enumerate(getattr(state, "tool_history", ()) or ())
        if isinstance(entry, Mapping)
        and str(entry.get("step_id") or "") == str(getattr(record, "step_id", ""))
        and isinstance(entry.get("result"), Mapping)
    ]
    if not relevant:
        return True
    return any(
        classify_failure(entry["result"]) is FailureClass.LOCAL
        and not _later_recovery(state, index, entry)
        and not (callable(permits) and permits(index + 1))
        for index, entry in relevant
    )


def _local_failure_requires_terminal(orchestrator: Any, *, include_invocation_history: bool) -> bool:
    state = orchestrator.agent_state
    semantics = getattr(state, "task_semantics", None)
    permits = getattr(semantics, "failure_observation_permitted", None)
    if include_invocation_history and any(
        not permitted for _, _, permitted in unrecovered_local_failure_observations(state)
    ):
        return True
    records = getattr(state, "step_records", {})
    if not isinstance(records, dict):
        return False
    failed_statuses = {StepStatus.FAILED, StepStatus.BLOCKED, StepStatus.UNVERIFIED}
    return any(
        _step_failure_requires_terminal(state, record, permits)
        for record in records.values()
        if getattr(record, "status", None) in failed_statuses
    )


def _hard_failure_from_state(
    orchestrator: Any,
    state: Any,
    hard_checker: Any,
    failure_checker: Any,
    include_invocation_history: bool,
) -> bool:
    task_failed = bool(getattr(orchestrator, "_task_failed", False))
    if callable(hard_checker):
        try:
            return bool(hard_checker(task_failed=task_failed))
        except TypeError:
            return bool(hard_checker())
    if callable(failure_checker):
        try:
            return bool(
                failure_checker(
                    task_failed=task_failed,
                    include_invocation_history=include_invocation_history,
                    hard_only=True,
                )
            )
        except TypeError:
            return False
    return task_failed


def _task_failed_without_fallback(state: Any, orchestrator: Any, failure_checker: Any, include_invocation_history: bool) -> bool:
    if not getattr(orchestrator, "_task_failed", False) or not callable(failure_checker):
        return False
    try:
        unresolved = failure_checker(
            task_failed=True,
            include_invocation_history=include_invocation_history,
            hard_only=False,
        )
    except TypeError:
        unresolved = True
    return bool(unresolved and not local_failure_permitted(state))


def _last_result_is_terminal(
    orchestrator: Any,
    state: Any,
    hard_checker: Any,
) -> bool:
    result = getattr(state, "last_result", None)
    if not isinstance(result, Mapping):
        return bool(getattr(orchestrator, "_task_failed", False)) and not callable(hard_checker)
    classification = classify_failure(result)
    if classification is FailureClass.HARD:
        return True
    if classification is FailureClass.LOCAL:
        local_failure = _local_failure_requires_terminal(orchestrator, include_invocation_history=True)
        return local_failure or not local_failure_permitted(state)
    return str(result.get("status") or "") in NON_SUCCESS_STATUSES


def terminal_failure(
    orchestrator: Any,
    *,
    include_invocation_history: bool = False,
    hard_only: bool = False,
) -> bool:
    state = orchestrator.agent_state
    hard_checker = getattr(state, "has_unrecovered_hard_task_failures", None)
    failure_checker = getattr(state, "has_unrecovered_task_failures", None)
    if _hard_failure_from_state(
        orchestrator,
        state,
        hard_checker,
        failure_checker,
        include_invocation_history,
    ):
        return True
    if hard_only:
        return False
    if _task_failed_without_fallback(state, orchestrator, failure_checker, include_invocation_history):
        return True
    if _local_failure_requires_terminal(
        orchestrator,
        include_invocation_history=include_invocation_history,
    ):
        return True
    return _last_result_is_terminal(orchestrator, state, hard_checker)

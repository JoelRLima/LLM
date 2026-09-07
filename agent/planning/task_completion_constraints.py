"""Constraint evidence helpers for canonical task completion."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent.runtime.mutation_evidence import project_mutation_evidence


def _constraint_owner(orchestrator: Any) -> Any:
    admitted = getattr(orchestrator, "_admitted_intent", None)
    if admitted is not None:
        return admitted
    return getattr(getattr(orchestrator, "agent_state", None), "task_semantics", None)


def _result_success(record: Any) -> bool:
    if isinstance(record, Mapping):
        if record.get("ok") is True:
            return True
        raw_status = record.get("status")
    else:
        if getattr(record, "ok", False) is True:
            return True
        raw_status = getattr(record, "status", None)
    return str(getattr(raw_status, "value", raw_status)).casefold() == "succeeded"


def _history_results(orchestrator: Any) -> tuple[Any, ...]:
    state = orchestrator.agent_state
    results: list[Any] = []
    for item in getattr(state, "tool_history", ()) or ():
        if isinstance(item, Mapping) and "result" in item:
            results.append(item["result"])
    last_result = getattr(state, "last_result", None)
    if last_result is not None:
        results.append(last_result)
    return tuple(results)


def required_validation_satisfied(orchestrator: Any) -> bool:
    """Return whether an admitted required-validation constraint has evidence."""

    owner = _constraint_owner(orchestrator)
    if not bool(getattr(owner, "requires_validation", False)):
        return True
    if not tuple(
        getattr(owner, "admitted_effects", ())
        or getattr(owner, "requested_effects", ())
        or ()
    ):
        return True
    mutation_seen = False
    for result in _history_results(orchestrator):
        evidence = project_mutation_evidence(result)
        if not (evidence.attempted or evidence.occurred or evidence.survives):
            continue
        mutation_seen = True
        if (
            isinstance(evidence.validation_status, str)
            and evidence.validation_status.casefold() == "passed"
            and _result_success(result)
        ):
            return True
    # No mutation/proposal has proceeded yet; pending-effect review remains
    # responsible for rejecting an incomplete task.  Once it has proceeded,
    # missing validation is a hard completion blocker.
    return not mutation_seen


def proposal_only_mutation_observed(orchestrator: Any) -> bool:
    owner = _constraint_owner(orchestrator)
    if not bool(getattr(owner, "proposal_only", False)):
        return False
    for result in _history_results(orchestrator):
        evidence = project_mutation_evidence(result)
        if evidence.occurred or evidence.survives:
            return True
    return False


def emit_completion_constraint_audit(orchestrator: Any, review: Any) -> None:
    state = getattr(orchestrator, "agent_state", None)
    metadata = getattr(getattr(orchestrator, "_task_execution_context", None), "metadata", {})
    if not (
        getattr(state, "w14_semantic_task", False) is True
        or getattr(orchestrator, "_admitted_intent", None) is not None
        or (isinstance(metadata, Mapping) and metadata.get("w14_semantic_task") is True)
    ):
        return
    emit = getattr(orchestrator, "_emit", None)
    if not callable(emit):
        return
    try:
        emit(
            "constraint_completion_checked",
            {
                "accepted": review.accepted,
                "reason_code": review.reason_code,
                "pending_effects": list(review.pending_effects),
                "pending_obligation_ids": [item.id for item in review.pending_obligations],
                "blocked_obligation_ids": [item.id for item in review.blocked_obligations],
                "prohibited_effects": list(review.prohibited_effects),
                "unrequested_effects": list(review.unrequested_effects),
            },
        )
    except (RuntimeError, TypeError, ValueError):
        return


__all__ = [
    "emit_completion_constraint_audit",
    "proposal_only_mutation_observed",
    "required_validation_satisfied",
]

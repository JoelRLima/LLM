"""Constraint evidence helpers for canonical task completion."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from llm_agent.agent.runtime.mutation_evidence import project_mutation_evidence
from llm_agent.agent.runtime.validation_evidence import current_validation_evidence
from llm_agent.agent.tools.result_adapter import (
    ensure_canonical_result,
    result_explicitly_succeeded,
)


def _constraint_owner(orchestrator: Any) -> Any:
    admitted = getattr(orchestrator, "_admitted_intent", None)
    if admitted is not None:
        return admitted
    return getattr(getattr(orchestrator, "agent_state", None), "task_semantics", None)


def _history_results(orchestrator: Any) -> tuple[Any, ...]:
    state = orchestrator.agent_state
    results: list[Any] = []
    history = getattr(state, "tool_history", ()) or ()
    for item in history:
        if isinstance(item, Mapping) and "result" in item:
            results.append(_canonical_success_result(item["result"]))
    if history:
        return tuple(results)
    last_result = getattr(state, "last_result", None)
    if last_result is not None:
        results.append(_canonical_success_result(last_result))
    return tuple(results)


def _canonical_success_result(value: Any) -> Any:
    """Adapt only explicitly successful legacy mappings at the planning boundary."""
    if isinstance(value, Mapping) and result_explicitly_succeeded(value):
        return ensure_canonical_result(value)
    return value


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
    return current_validation_evidence(_history_results(orchestrator)).satisfied


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

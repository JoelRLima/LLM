"""Canonical task-level completion policy for the linear execution path."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent.planning.completion_observations import (
    eligible_waiver_observations,
    observation_references,
    refresh_executed_effects,
    terminal_failure,
)
from agent.planning.plan_builder import PlanningDecisionKind
from agent.planning.reasoning_boundary import BoundaryContinuationResult
from agent.planning.reasoning_boundary import (
    continue_after_reasoning_boundary as _reasoning_boundary,
)
from agent.planning.requested_effects import infer_requested_effects
from agent.planning.task_completion_dispatch import accept_review, reject_review
from agent.planning.task_completion_types import CompletionDisposition
from agent.planning.task_semantics import TaskObligation, TaskSemantics
from agent.planning.task_terminal import (
    _set_terminal,
    mark_reasoning_boundary_blocked,
    mark_terminal_blocked,
    mark_terminal_cancelled,
    mark_terminal_failure,
    mark_unfinished_effect,
    mark_unfinished_obligation,
)


@dataclass(frozen=True, slots=True)
class CompletionReview:
    """Read-only decision produced by the canonical completion owner."""

    accepted: bool
    reason_code: str | None = None
    existing_disposition: str | None = None
    pending_effects: tuple[str, ...] = ()
    pending_obligations: tuple[TaskObligation, ...] = ()
    blocked_obligations: tuple[TaskObligation, ...] = ()
    prohibited_effects: tuple[str, ...] = ()
    unrequested_effects: tuple[str, ...] = ()
    unrecovered_failure: bool = False


MAX_CONTINUATION_ATTEMPTS = 1


def initialize_task_progression(orchestrator: Any, objective: str) -> None:
    state = orchestrator.agent_state
    if hasattr(state, "initialize_task_semantics") and isinstance(
        getattr(state, "task_semantics", None), TaskSemantics
    ):
        state.initialize_task_semantics(objective)
        state.reset_task_progression(
            state.task_semantics.requested_effects,
            preserve_semantics=True,
        )
    else:
        requested = infer_requested_effects(objective)
        state.reset_task_progression(requested)
    orchestrator.agent_state.reasoning_last_history_count = len(
        orchestrator.agent_state.tool_history
    )


def bind_effect_waiver(orchestrator: Any, observation_index: int, *, effects: tuple[str, ...] | None = None, source: str = "continuation") -> bool:
    match = next((item for index, item in eligible_waiver_observations(orchestrator) if index == observation_index), None)
    pending = orchestrator.agent_state.pending_effects()
    if match is None or not pending:
        return False
    selected = pending if effects is None else effects
    if not selected or any(effect not in pending for effect in selected):
        return False
    for effect in selected:
        if isinstance(getattr(orchestrator.agent_state, "task_semantics", None), TaskSemantics):
            semantics = orchestrator.agent_state.task_semantics
            register = getattr(semantics, "register_observation", None)
            if callable(register):
                register(
                    str(match.get("tool", "")),
                    match["result"],
                    evidence_ref=observation_index,
                    args=match.get("args") if isinstance(match.get("args"), dict) else {},
                )
            orchestrator.agent_state.waive_effect(
                effect,
                evidence_ref=observation_index,
                effect_authority=orchestrator,
            )
        else:
            orchestrator.agent_state.waive_effect(effect)
    orchestrator._emit("effect_waiver_bound", {"effects": list(selected), "observation_index": observation_index, "invocation_id": match.get("invocation_id"), "source": source})
    return True


def needs_effect_continuation(orchestrator: Any, objective: str) -> bool:
    del objective
    refresh_executed_effects(orchestrator)
    state = orchestrator.agent_state
    return not terminal_failure(orchestrator) and bool(state.pending_effects()) and state.continuation_attempts < MAX_CONTINUATION_ATTEMPTS


def review_task_completion(orchestrator: Any) -> CompletionReview:
    """Evaluate completion without mutating terminal lifecycle state."""

    refresh_executed_effects(orchestrator)
    state = orchestrator.agent_state
    existing = getattr(state, "terminal_disposition", None)
    if existing == "succeeded":
        existing = None
    pending_effects = tuple(getattr(state, "pending_effects", lambda: ())())
    pending_obligations = tuple(getattr(state, "pending_obligations", lambda: ())())
    blocked_obligations = tuple(getattr(state, "blocked_obligations", lambda: ())())
    prohibited = tuple(
        getattr(state, "prohibited_effects_occurred", lambda: ())()
    )
    unrequested = tuple(
        getattr(state, "unrequested_effects", lambda: ())()
    )
    hard_failure = terminal_failure(
        orchestrator,
        include_invocation_history=True,
        hard_only=True,
    )
    reason = _completion_block_reason(
        orchestrator,
        state,
        existing,
        pending_effects,
        pending_obligations,
        blocked_obligations,
        prohibited,
        unrequested,
        hard_failure,
    )
    if reason is None:
        return CompletionReview(
            accepted=True,
            existing_disposition=existing,
        )
    return CompletionReview(
        accepted=False,
        reason_code=reason,
        existing_disposition=existing,
        pending_effects=pending_effects,
        pending_obligations=pending_obligations,
        blocked_obligations=blocked_obligations,
        prohibited_effects=prohibited,
        unrequested_effects=unrequested,
        unrecovered_failure=hard_failure or reason == "terminal_failure",
    )


def _completion_block_reason(
    orchestrator: Any,
    state: Any,
    existing: str | None,
    pending_effects: tuple[str, ...],
    pending_obligations: tuple[TaskObligation, ...],
    blocked_obligations: tuple[TaskObligation, ...],
    prohibited: tuple[str, ...],
    unrequested: tuple[str, ...],
    hard_failure: bool,
) -> str | None:
    checks = (
        ("cancelled", lambda: bool(getattr(orchestrator, "_cancelled", False))),
        ("terminal_failure", lambda: hard_failure),
        ("prohibited_effect_occurred", lambda: bool(prohibited)),
        ("unrequested_effect_occurred", lambda: bool(unrequested)),
        (
            "existing_terminal",
            lambda: existing is not None and existing != CompletionDisposition.COMPLETE.value,
        ),
        (
            "obligation_evidence_missing",
            lambda: not getattr(state, "terminal_evidence_complete", lambda: True)(),
        ),
        (
            "terminal_failure",
            lambda: terminal_failure(orchestrator, include_invocation_history=True),
        ),
        ("task_obligation_blocked", lambda: bool(blocked_obligations)),
        ("requested_effect_pending", lambda: bool(pending_effects)),
        ("task_obligation_pending", lambda: bool(pending_obligations)),
    )
    return next((reason for reason, condition in checks if condition()), None)


def continue_after_reasoning_boundary(orchestrator: Any, objective: str) -> BoundaryContinuationResult:
    """Translate a pure reasoning decision through canonical completion."""

    boundary = _reasoning_boundary(orchestrator, objective)
    if boundary.blocked:
        return BoundaryContinuationResult(
            answer=mark_reasoning_boundary_blocked(orchestrator, objective),
            blocked=True,
        )
    if boundary.completed:
        blocker = allow_linear_completion(orchestrator, objective)
        return BoundaryContinuationResult(answer=blocker, completed=blocker is None)
    return boundary
def allow_linear_completion(orchestrator: Any, objective: str) -> str | None:
    existing = getattr(orchestrator.agent_state, "terminal_disposition", None)
    if existing == "succeeded":
        _set_terminal(orchestrator.agent_state, None)
        existing = None
    review = review_task_completion(orchestrator)
    if review.accepted:
        return accept_review(orchestrator, existing)
    return reject_review(orchestrator, objective, review)
def complete_direct_answer(orchestrator: Any, objective: str, answer: str) -> str:
    return allow_linear_completion(orchestrator, objective) or answer


def continue_after_observation(orchestrator: Any, objective: str) -> str | None:
    state = orchestrator.agent_state
    state.continuation_attempts += 1
    refresh_executed_effects(orchestrator)
    executed = ", ".join(state.executed_effects) or "nenhum efeito de escrita executado"
    try:
        continuation = orchestrator.plan_builder.continue_after_observation(objective, executed, observation_references(orchestrator))
    except Exception:
        return mark_unfinished_effect(orchestrator, objective)
    if continuation.kind is PlanningDecisionKind.COMPLETE:
        index = continuation.waiver_observation_index
        if index is None or not bind_effect_waiver(orchestrator, index):
            return mark_unfinished_effect(orchestrator, objective)
        # Waiving the effect resolves only the effect obligation.  Overall
        # task completion must still pass through the post-plan reasoning
        # boundary in the execution loop.
        return None
    if continuation.kind is not PlanningDecisionKind.EXECUTE or not continuation.plan:
        return mark_unfinished_effect(orchestrator, objective)
    orchestrator._emit("continuation_plan_proposed", {"steps": len(continuation.plan), "plan": continuation.plan})
    try:
        validated = orchestrator.execution_gateway.extend_validated_plan(continuation.plan, objective)
    except Exception:
        validated = None
    return None if validated is not None else mark_unfinished_effect(orchestrator, objective)


__all__ = [
    "CompletionDisposition", "CompletionReview", "BoundaryContinuationResult", "MAX_CONTINUATION_ATTEMPTS",
    "allow_linear_completion", "bind_effect_waiver", "complete_direct_answer",
    "continue_after_observation", "continue_after_reasoning_boundary", "infer_requested_effects",
    "initialize_task_progression",
    "mark_terminal_failure", "mark_terminal_blocked", "mark_terminal_cancelled",
    "mark_unfinished_effect", "mark_reasoning_boundary_blocked",
    "mark_unfinished_obligation",
    "needs_effect_continuation", "refresh_executed_effects", "review_task_completion",
]

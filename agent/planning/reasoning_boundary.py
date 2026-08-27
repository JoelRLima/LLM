"""Single bounded continuation after an explicit plan frontier."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Callable, cast

from agent.planning.plan_builder import PlanningDecisionKind
from agent.planning.task_semantics import TaskSemanticsError
from agent.runtime.budget import BudgetExhausted
from agent.runtime.limits import runtime_limit_values

_EPHEMERAL_FIELDS = frozenset(
    {"invocation_id", "step_id", "request_id", "timestamp", "created_at", "updated_at", "token_count"}
)


@dataclass(frozen=True)
class BoundaryContinuationResult:
    answer: str | None = None
    extended: bool = False
    completed: bool = False
    blocked: bool = False


def _apply_canonical_review(orchestrator: Any, continuation: Any) -> bool:
    raw = getattr(continuation, "review_obligations", None)
    if raw is None:
        return True
    report_reviewer = getattr(orchestrator.agent_state, "review_task_obligations_report", None)
    legacy_reviewer = getattr(orchestrator.agent_state, "review_task_obligations", None)
    if not callable(report_reviewer) and not callable(legacy_reviewer):
        return False
    try:
        if callable(report_reviewer):
            review = report_reviewer(raw, source="canonical_review")
            added = review.accepted
            rejected = review.rejected
        else:
            review = cast(Callable[..., Any], legacy_reviewer)(
                raw,
                source="canonical_review",
                collect_rejections=True,
            )
            added = tuple(review)
            rejected = ()
    except (TaskSemanticsError, TypeError, ValueError):
        return False
    emit = getattr(orchestrator, "_emit", None)
    if callable(emit):
        emit("canonical_review_amendment", {"added": len(added)})
        if rejected:
            emit(
                "canonical_review_rejected",
                {
                    "rejected": len(rejected),
                    "reasons": [item.reason for item in rejected],
                },
            )
    objective = str(getattr(orchestrator.agent_state, "objective", "") or "").casefold()
    required_rejection = any(
        _rejection_is_required(item, objective, orchestrator.agent_state)
        for item in rejected
    )
    if required_rejection and callable(emit):
        emit("canonical_review_required_rejection", {"count": 1})
    # Unrelated model additions remain safely ignored for compatibility, but
    # a rejected obligation grounded in the user objective cannot coexist with
    # a COMPLETE continuation.
    return not required_rejection


def _rejection_is_required(rejection: Any, objective: str, state: Any) -> bool:
    proposal = getattr(rejection, "proposal", None)
    if not isinstance(proposal, Mapping):
        return False
    effect = proposal.get("effect")
    requested = set(getattr(state, "requested_effects", ()))
    if isinstance(effect, str) and effect.casefold() in requested:
        return True
    for key in ("target", "query", "fallback_target"):
        value = proposal.get(key)
        if isinstance(value, str) and value.strip().casefold() in objective:
            return True
    operands = proposal.get("operands")
    if isinstance(operands, (list, tuple)) and any(
        isinstance(value, str) and value.strip().casefold() in objective
        for value in operands
    ):
        return True
    return False


def _semantic_value(value: Any, field: str | None = None) -> Any:
    """Normalize canonical state while excluding identity/timing metadata."""

    if field in _EPHEMERAL_FIELDS:
        return None
    if isinstance(value, Mapping):
        return {
            str(key): _semantic_value(item, str(key))
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            if str(key) not in _EPHEMERAL_FIELDS
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_semantic_value(item) for item in value]
    if value is None or type(value) in (str, int, float, bool):
        return value
    return str(value)[:512]


def reasoning_progress_fingerprint(history: Sequence[Mapping[str, Any]]) -> str:
    """Hash semantic work/results, never ephemeral invocation identities."""

    entry_digests: set[bytes] = set()
    for item in history:
        raw_result = item.get("result")
        result: Mapping[str, Any] = raw_result if isinstance(raw_result, Mapping) else {}
        semantic = {
            "tool": item.get("tool", ""),
            "args": item.get("args", {}),
            "result": {
                key: result.get(key)
                for key in ("status", "ok", "executed", "data", "error_code", "complete", "truncated", "artifacts")
                if key in result
            },
        }
        encoded = json.dumps(
            _semantic_value(semantic),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        entry_digests.add(hashlib.sha256(encoded.encode("utf-8")).digest())
    digest = hashlib.sha256()
    for entry_digest in sorted(entry_digests):
        digest.update(entry_digest)
    return digest.hexdigest()


def continue_after_reasoning_boundary(orchestrator: Any, objective: str) -> BoundaryContinuationResult:
    state = orchestrator.agent_state
    config = getattr(getattr(orchestrator, "session", None), "config", {}) or {}
    limit = runtime_limit_values(config)["max_reasoning_turns"]
    history = state.tool_history
    stored_cursor = int(getattr(state, "reasoning_last_history_count", -1))
    uninitialized_cursor = stored_cursor < 0
    cursor = 0 if uninitialized_cursor else min(stored_cursor, len(history))
    window = history[cursor:]
    progress = reasoning_progress_fingerprint(window)
    turns_used = int(getattr(state, "reasoning_turns_used", 0))
    last_progress = getattr(state, "reasoning_last_progress_token", None)
    if _boundary_is_blocked(
        turns_used, limit, uninitialized_cursor, window, last_progress, progress
    ):
        return BoundaryContinuationResult(blocked=True)
    state.reasoning_turns_used = turns_used + 1
    state.reasoning_last_history_count = len(history)
    state.reasoning_last_progress_token = progress
    continuation = _request_continuation(orchestrator, objective)
    if continuation is None or continuation.kind is PlanningDecisionKind.FAIL:
        return BoundaryContinuationResult(blocked=True)
    if not _apply_canonical_review(orchestrator, continuation):
        return BoundaryContinuationResult(blocked=True)
    return _project_continuation(orchestrator, continuation, objective)


def _boundary_is_blocked(
    turns_used: int,
    limit: int,
    uninitialized_cursor: bool,
    window: Sequence[Mapping[str, Any]],
    last_progress: str | None,
    progress: str,
) -> bool:
    return turns_used >= limit or (
        not uninitialized_cursor and not window
    ) or (last_progress is not None and last_progress == progress)


def _request_continuation(orchestrator: Any, objective: str) -> Any | None:
    try:
        return orchestrator.plan_builder.continue_after_reasoning_boundary(objective)
    except BudgetExhausted:
        raise
    except Exception:
        return None


def _project_continuation(
    orchestrator: Any, continuation: Any, objective: str,
) -> BoundaryContinuationResult:
    if continuation.kind is PlanningDecisionKind.COMPLETE:
        return BoundaryContinuationResult(completed=True)
    if continuation.kind is PlanningDecisionKind.BLOCK:
        return BoundaryContinuationResult(blocked=True)
    if continuation.kind is not PlanningDecisionKind.EXECUTE or not continuation.plan:
        return BoundaryContinuationResult(blocked=True)
    orchestrator._emit("reasoning_boundary_plan_proposed", {"steps": len(continuation.plan), "plan": continuation.plan})
    return _extend_plan(orchestrator, continuation.plan, objective)


def _extend_plan(
    orchestrator: Any, plan: Any, objective: str,
) -> BoundaryContinuationResult:
    extender = getattr(orchestrator.execution_gateway, "extend_validated_plan", None)
    if not callable(extender):
        return BoundaryContinuationResult(blocked=True)
    try:
        try:
            validated = extender(
                plan,
                objective,
                allow_conditional_preview=True,
            )
        except TypeError as exc:
            # Small compatibility test gateways (and supported external
            # adapters) may still expose the pre-preview two-argument seam.
            # Only retry that explicitly narrow signature mismatch; the
            # production gateway accepts the keyword and never takes this
            # path.
            if "allow_conditional_preview" not in str(exc):
                raise
            validated = extender(plan, objective)
    except BudgetExhausted:
        raise
    except Exception:
        validated = None
    if validated is None:
        return BoundaryContinuationResult(blocked=True)
    return BoundaryContinuationResult(extended=True)


__all__ = [
    "BoundaryContinuationResult",
    "continue_after_reasoning_boundary",
]

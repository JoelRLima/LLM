"""Single bounded continuation after an explicit plan frontier."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from agent.planning.plan_builder import PlanningDecisionKind
from agent.runtime.config import DEFAULT_COST_WATCHDOG

_EPHEMERAL_FIELDS = frozenset(
    {"invocation_id", "step_id", "request_id", "timestamp", "created_at", "updated_at", "token_count"}
)


@dataclass(frozen=True)
class BoundaryContinuationResult:
    answer: str | None = None
    extended: bool = False
    completed: bool = False
    blocked: bool = False


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
    limit = int(config.get("max_reasoning_turns", DEFAULT_COST_WATCHDOG["max_reasoning_turns"]))
    history = state.tool_history
    stored_cursor = int(getattr(state, "reasoning_last_history_count", -1))
    uninitialized_cursor = stored_cursor < 0
    cursor = 0 if uninitialized_cursor else min(stored_cursor, len(history))
    window = history[cursor:]
    progress = reasoning_progress_fingerprint(window)
    turns_used = int(getattr(state, "reasoning_turns_used", 0))
    last_progress = getattr(state, "reasoning_last_progress_token", None)
    if turns_used >= limit or (
        not uninitialized_cursor and not window
    ) or (last_progress is not None and last_progress == progress):
        return BoundaryContinuationResult(blocked=True)
    state.reasoning_turns_used = turns_used + 1
    state.reasoning_last_history_count = len(history)
    state.reasoning_last_progress_token = progress
    try:
        continuation = orchestrator.plan_builder.continue_after_reasoning_boundary(objective)
    except Exception:
        return BoundaryContinuationResult(blocked=True)
    if not continuation or continuation.kind is PlanningDecisionKind.FAIL:
        return BoundaryContinuationResult(blocked=True)
    if continuation.kind is PlanningDecisionKind.COMPLETE:
        return BoundaryContinuationResult(completed=True)
    if continuation.kind is PlanningDecisionKind.BLOCK:
        return BoundaryContinuationResult(blocked=True)
    if continuation.kind is not PlanningDecisionKind.EXECUTE or not continuation.plan:
        return BoundaryContinuationResult(blocked=True)
    orchestrator._emit("reasoning_boundary_plan_proposed", {"steps": len(continuation.plan), "plan": continuation.plan})
    try:
        validated = orchestrator.execution_gateway.extend_validated_plan(continuation.plan, objective)
    except Exception:
        validated = None
    if validated is None:
        return BoundaryContinuationResult(blocked=True)
    return BoundaryContinuationResult(extended=True)


__all__ = [
    "BoundaryContinuationResult",
    "continue_after_reasoning_boundary",
]

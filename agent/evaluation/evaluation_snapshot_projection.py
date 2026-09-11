"""Complete evaluation projection owned by the canonical run state."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from agent.evaluation.evidence import sanitize_evidence
from agent.planning.plan_model import serialize_plan
from agent.reporting.evaluation_arg_projection import project_evaluation_args
from agent.reporting.run_projection_facts import thaw_projection

_STATE_HISTORY_FIELDS = (
    "run_id",
    "root_task_id",
    "task_id",
    "parent_task_id",
    "node_id",
    "plan_id",
    "step_id",
    "invocation_id",
)


def _legacy_result(value: Any) -> Mapping[str, Any]:
    to_legacy = getattr(value, "to_legacy_dict", None)
    if callable(to_legacy):
        projected = to_legacy(include_details=True)
        return projected if isinstance(projected, Mapping) else {}
    if isinstance(value, Mapping):
        return value
    return {}


def _state_invocation_history(state: Any) -> list[dict[str, Any]]:
    history = getattr(state, "tool_history", ()) or ()
    projected: list[dict[str, Any]] = []
    for raw_entry in list(history)[:50]:
        if not isinstance(raw_entry, Mapping):
            continue
        tool = raw_entry.get("tool")
        if not isinstance(tool, str) or not tool:
            continue
        invocation: dict[str, Any] = {
            "tool": tool,
            "args": project_evaluation_args(raw_entry.get("args")),
            "result": sanitize_evidence(dict(_legacy_result(raw_entry.get("result")))),
        }
        for field in _STATE_HISTORY_FIELDS:
            value = raw_entry.get(field)
            if isinstance(value, str) and value:
                invocation[field] = value
        projected.append(invocation)
    return projected


def _state_canonical_plan(state: Any) -> list[Any] | None:
    plan = getattr(state, "plan", None)
    if plan is None:
        return None
    try:
        serialized = serialize_plan(plan)
    except (TypeError, ValueError):
        return None
    return cast(list[Any], sanitize_evidence(serialized))


def snapshot_evaluation_projection(
    snapshot: Any,
    *,
    state: Any = None,
) -> dict[str, Any]:
    """Project evaluation evidence, retaining complete facts from the run owner."""

    facts = snapshot.projection_facts
    state_history = _state_invocation_history(state) if state is not None else []
    state_plan = _state_canonical_plan(state) if state is not None else None
    return {
        "history": state_history or [thaw_projection(item) for item in facts.invocation_evidence],
        "canonical_plan": (
            state_plan if state_plan is not None else thaw_projection(facts.canonical_plan)
        ),
        "route_events": [thaw_projection(item) for item in facts.route_events],
        "validation_events": [thaw_projection(item) for item in facts.validation_events],
        "code_outcome": thaw_projection(facts.code_outcome),
        "validation_detail": thaw_projection(facts.validation_detail),
        "output_chars": facts.output_chars,
        "output_truncated": facts.output_truncated,
    }

__all__ = ["snapshot_evaluation_projection"]

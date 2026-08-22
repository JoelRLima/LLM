"""Causal scope selection for replanning evidence."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def scoped_replan_observations(
    orchestrator: Any,
    candidate_steps: Sequence[Mapping[str, Any]],
) -> tuple[str | None, Sequence[Mapping[str, Any]]]:
    state = getattr(orchestrator, "agent_state", None)
    current_ids = {
        str(step.get("_step_id"))
        for step in getattr(state, "plan", ())
        if isinstance(step, Mapping) and step.get("_step_id")
    }
    candidate_ids = {
        str(step.get("_step_id"))
        for step in candidate_steps
        if isinstance(step, Mapping) and step.get("_step_id")
    }
    plan_id = getattr(state, "plan_identity", None)
    if not plan_id or not current_ids.intersection(candidate_ids):
        return None, ()
    return plan_id, getattr(state, "tool_history", ())


__all__ = ["scoped_replan_observations"]

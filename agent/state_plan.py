"""Canonical plan-step identity normalization."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any, cast

from agent.contracts import PlanStep


def canonicalize_plan_steps(
    plan: Sequence[Mapping[str, Any]],
    new_step_id: Callable[[], str],
    *,
    preserve_step_ids: bool = True,
) -> list[PlanStep]:
    normalized: list[PlanStep] = []
    seen: set[str] = set()
    for raw_step in plan:
        step = dict(raw_step)
        if "tool" in step or "args" in step:
            args = step.get("args")
            step["args"] = dict(args) if isinstance(args, dict) else {}
        candidate = str(step.get("_step_id") or "") if preserve_step_ids else ""
        step_id = candidate if candidate and candidate not in seen else new_step_id()
        step["_step_id"] = step_id
        seen.add(step_id)
        normalized.append(cast(PlanStep, step))
    return normalized

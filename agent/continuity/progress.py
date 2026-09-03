"""Plan-progress projection used by the read-only continuity classifier."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent.checkpoint_plan_consistency import plan_consistency_error
from agent.continuity.models import PlanProgress

_STEP_STATUSES = frozenset(
    {"pending", "running", "completed", "failed", "skipped", "blocked", "cancelled", "unverified"}
)
_TERMINAL_STEP_STATUSES = frozenset(
    {"completed", "failed", "skipped", "blocked", "cancelled", "unverified"}
)


def project_plan_progress(checkpoint: Mapping[str, Any]) -> PlanProgress:
    plan = checkpoint.get("plan")
    records = checkpoint.get("step_records")
    if not isinstance(plan, list) or any(not isinstance(step, Mapping) for step in plan):
        raise ValueError("checkpoint plan is invalid")
    if not isinstance(records, list) or any(not isinstance(record, Mapping) for record in records):
        raise ValueError("checkpoint step records are invalid")
    if plan_consistency_error(
        plan,
        records,
        checkpoint.get("plan_step", 0),
        checkpoint.get("current_step_id"),
    ) is not None:
        raise ValueError("checkpoint plan progress is inconsistent")
    statuses: list[str] = []
    for record in records:
        status = record.get("status", "pending")
        if not isinstance(status, str) or status not in _STEP_STATUSES:
            raise ValueError("checkpoint step status is invalid")
        statuses.append(status)
    total_steps = max(len(plan), len(statuses))
    terminal_steps = sum(status in _TERMINAL_STEP_STATUSES for status in statuses)
    completed_steps = statuses.count("completed")
    pending_steps = max(0, total_steps - terminal_steps)
    return PlanProgress(
        total_steps=total_steps,
        completed_steps=min(completed_steps, total_steps),
        pending_steps=min(pending_steps, total_steps),
        current_step=checkpoint.get("plan_step", 0),
        current_step_id=checkpoint.get("current_step_id"),
    )


__all__ = ["project_plan_progress"]

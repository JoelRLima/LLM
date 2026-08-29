"""Canonical plan-step identity normalization."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from agent.planning.plan_model import Plan


def canonicalize_plan_steps(
    plan: Sequence[Mapping[str, Any]],
    new_step_id: Callable[[], str],
    *,
    preserve_step_ids: bool = True,
) -> Plan:
    """Compatibility wrapper for the typed plan decoder."""

    return Plan.from_raw(
        plan,
        new_step_id=new_step_id,
        preserve_step_ids=preserve_step_ids,
    )

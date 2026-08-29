"""Causal plan-instance identity checks."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from agent.planning.plan_model import Plan


def validate_plan_identities(plan: Plan | Sequence[Any]) -> list[str]:
    """Keep stable step IDs unique within one causal plan instance."""

    if isinstance(plan, Plan):
        # Plan.__post_init__ is the canonical typed identity owner; reaching
        # this branch means uniqueness was already admitted.
        return []

    errors: list[str] = []
    seen: dict[str, int] = {}
    for index, step in enumerate(plan):
        if not isinstance(step, Mapping) or "_step_id" not in step:
            continue
        value = step.get("_step_id")
        if not isinstance(value, str) or not value.strip():
            errors.append(f"Passo {index + 1} possui _step_id invalido.")
            continue
        if value in seen:
            errors.append(
                f"_step_id duplicado entre os passos {seen[value] + 1} e {index + 1}."
            )
            continue
        seen[value] = index
    return errors


__all__ = ["validate_plan_identities"]

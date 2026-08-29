"""Plan-validator bridge for the closed deferred-condition shape."""

from __future__ import annotations

from typing import Any, Callable

from agent.planning.deferred_condition import (
    is_deferred_condition,
    validate_deferred_condition,
)
from agent.planning.plan_model import DeferredConditionStep, Plan, PlanDecodeError


def validate_deferred_items(
    plan: Plan | list[dict[str, Any]],
    objective: str,
    canonical_references: bool,
    step_validator: Callable[[Any], str | None],
    *,
    deferred_step_validator: Callable[[Any], str | None] | None = None,
) -> list[str]:
    errors: list[str] = []
    if not isinstance(plan, Plan):
        try:
            plan = Plan.from_raw(plan)
        except (PlanDecodeError, TypeError, ValueError) as exc:
            return [f"Plano deferred invÃ¡lido: {exc}."]
    for index, step in enumerate(plan.steps):
        if not is_deferred_condition(step):
            continue
        if isinstance(step, DeferredConditionStep):
            reference = step.observation_ref
            canonical_reference = reference.is_stable_id
            ordinal_reference = reference.is_ordinal
        else:
            continue
        if canonical_references and not canonical_reference:
            errors.append(
                f"Passo {index + 1} deferred inválido: observation_ref canônico ausente."
            )
            continue
        if not canonical_references and not ordinal_reference:
            errors.append(
                f"Passo {index + 1} deferred inválido: observation_ref deve ser ordinal local."
            )
            continue
        problem = validate_deferred_condition(step, index, plan, objective)
        if problem is None:
            # The branch is only a deferred control payload at this stage.
            # Its durable effect is revalidated immediately after the trusted
            # observation resolves the branch.  Keep the legacy one-argument
            # callback usable for callers outside PlanValidator.
            branch_validator = deferred_step_validator or step_validator
            branch = step.on_true
            problem = branch_validator(branch)
        if problem:
            errors.append(f"Passo {index + 1} deferred inválido: {problem}.")
    return errors

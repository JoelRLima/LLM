"""Plan-validator bridge for the closed deferred-condition shape."""

from __future__ import annotations

from typing import Any, Callable

from agent.planning.deferred_condition import (
    is_deferred_condition,
    validate_deferred_condition,
)


def validate_deferred_items(
    plan: list[dict[str, Any]],
    objective: str,
    canonical_references: bool,
    step_validator: Callable[[Any], str | None],
    *,
    deferred_step_validator: Callable[[Any], str | None] | None = None,
) -> list[str]:
    errors: list[str] = []
    for index, step in enumerate(plan):
        if not is_deferred_condition(step):
            continue
        reference = step.get("observation_ref")
        if canonical_references and (not isinstance(reference, str) or not reference):
            errors.append(
                f"Passo {index + 1} deferred inválido: observation_ref canônico ausente."
            )
            continue
        if not canonical_references and type(reference) is not int:
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
            problem = branch_validator(step.get("on_true"))
        if problem:
            errors.append(f"Passo {index + 1} deferred inválido: {problem}.")
    return errors

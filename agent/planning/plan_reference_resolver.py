"""Single resolver for previous typed-plan references."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from agent.planning.plan_model import Plan
from agent.planning.plan_model_types import (
    PlanReferenceError,
    PlanStepReference,
    ResultBinding,
)
from agent.planning.plan_step_types import DeferredConditionStep, PlanStep, ToolPlanStep


def _typed_steps(plan: Plan | Sequence[PlanStep]) -> tuple[PlanStep, ...]:
    steps = plan.steps if isinstance(plan, Plan) else tuple(plan)
    seen: set[str] = set()
    for step in steps:
        if not isinstance(step, (ToolPlanStep, DeferredConditionStep)):
            raise PlanReferenceError("resolver exige passos tipados")
        if step.step_id in seen:
            raise PlanReferenceError(f"_step_id duplicado: {step.step_id}")
        seen.add(step.step_id)
    return steps


def resolve_previous_step_reference(
    reference: PlanStepReference,
    index: int,
    plan: Plan | Sequence[PlanStep],
) -> int:
    """Resolve one reference to a strictly previous plan index."""

    steps = _typed_steps(plan)
    if type(index) is not int or index < 0 or index >= len(steps):
        raise PlanReferenceError("índice do passo consumidor inválido")
    if not isinstance(reference, PlanStepReference):
        raise PlanReferenceError("resolver exige PlanStepReference")
    if reference.ordinal is not None:
        candidate = reference.ordinal - 1
        if candidate < 0 or candidate >= index:
            raise PlanReferenceError("ordinal deve apontar para um passo anterior")
        return candidate
    assert reference.step_id is not None
    matches = [
        candidate
        for candidate, step in enumerate(steps[:index])
        if step.step_id == reference.step_id
    ]
    if len(matches) != 1:
        raise PlanReferenceError("ID estável deve apontar para exatamente um passo anterior")
    return matches[0]


def bind_plan_step_reference(
    reference: PlanStepReference,
    index: int,
    plan: Plan | Sequence[PlanStep],
) -> PlanStepReference:
    """Canonicalize an ordinal/ID reference to its producer stable ID."""

    steps = _typed_steps(plan)
    producer_index = resolve_previous_step_reference(reference, index, steps)
    return PlanStepReference.from_step_id(steps[producer_index].step_id)


def resolve_previous_step(
    reference: PlanStepReference,
    index: int,
    plan: Plan | Sequence[PlanStep],
) -> PlanStep:
    steps = _typed_steps(plan)
    return steps[resolve_previous_step_reference(reference, index, steps)]


def bind_plan_references(plan: Plan) -> Plan:
    """Bind every result/deferred reference once, preserving path semantics."""

    if not isinstance(plan, Plan):
        raise PlanReferenceError("bind_plan_references exige Plan")
    bound: list[PlanStep] = []
    for index, step in enumerate(plan.steps):
        if isinstance(step, ToolPlanStep):
            bindings = None
            if step.bindings is not None:
                bindings = {
                    target: replace(
                        binding,
                        from_step=bind_plan_step_reference(binding.from_step, index, plan),
                    )
                    for target, binding in step.bindings.items()
                }
            bound.append(replace(step, bindings=bindings))
        else:
            bound.append(
                replace(
                    step,
                    observation_ref=bind_plan_step_reference(
                        step.observation_ref, index, plan
                    ),
                )
            )
    return Plan(tuple(bound))


def resolve_result_binding_reference(
    binding: ResultBinding,
    index: int,
    plan: Plan | Sequence[PlanStep],
) -> int:
    return resolve_previous_step_reference(binding.from_step, index, plan)


def resolve_deferred_observation_reference(
    step: DeferredConditionStep,
    index: int,
    plan: Plan | Sequence[PlanStep],
) -> int:
    return resolve_previous_step_reference(step.observation_ref, index, plan)


__all__ = [
    "bind_plan_references",
    "bind_plan_step_reference",
    "resolve_deferred_observation_reference",
    "resolve_previous_step",
    "resolve_previous_step_reference",
    "resolve_result_binding_reference",
]

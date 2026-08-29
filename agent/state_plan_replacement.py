"""Canonical replacement helpers for AgentState's typed plan owner."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any, Dict, cast

from agent.planning.plan_model import Plan, PlanStep, PlanStepReference, ResultBinding, ToolPlanStep


def canonical_replacement_steps(
    state: Any,
    index: int,
    new_steps: Sequence[PlanStep | Mapping[str, Any]],
) -> tuple[PlanStep, ...]:
    candidate = (
        new_steps
        if isinstance(new_steps, Plan)
        else Plan.from_raw(
            cast(Sequence[Mapping[str, Any]], new_steps),
            new_step_id=state._new_step_id,
        )
    )
    retained_ids = {
        step.step_id
        for position, step in enumerate(state.plan.steps)
        if position != index
    }
    remap = _replacement_id_map(state, candidate, retained_ids)
    return tuple(_remap_replacement_step(step, remap) for step in candidate)


def _replacement_id_map(
    state: Any,
    candidate: Plan,
    retained_ids: set[str],
) -> Dict[str, str]:
    used = set(retained_ids)
    remap: Dict[str, str] = {}
    for step in candidate:
        step_id = step.step_id
        if step_id in used:
            fresh_id = _fresh_unused_step_id(state, used)
            remap[step_id] = fresh_id
            used.add(fresh_id)
        else:
            used.add(step_id)
    return remap


def _fresh_unused_step_id(state: Any, used: set[str]) -> str:
    while True:
        candidate = state._new_step_id()
        if candidate not in used:
            return cast(str, candidate)


def _remap_replacement_step(
    step: PlanStep,
    remap: Dict[str, str],
) -> PlanStep:
    step_id = remap.get(step.step_id, step.step_id)
    if isinstance(step, ToolPlanStep):
        bindings = (
            {
                target: _remap_binding(binding, remap)
                for target, binding in step.bindings.items()
            }
            if step.bindings is not None
            else None
        )
        return replace(step, step_id=step_id, bindings=bindings)
    return replace(
        step,
        step_id=step_id,
        observation_ref=_remap_reference(step.observation_ref, remap),
    )


def _remap_binding(binding: ResultBinding, remap: Dict[str, str]) -> ResultBinding:
    return replace(binding, from_step=_remap_reference(binding.from_step, remap))


def _remap_reference(
    reference: PlanStepReference,
    remap: Dict[str, str],
) -> PlanStepReference:
    if reference.step_id is None or reference.step_id not in remap:
        return reference
    return replace(reference, step_id=remap[reference.step_id])


__all__ = ["canonical_replacement_steps"]

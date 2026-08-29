"""Causal scope selection for replanning evidence."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from agent.planning.plan_admission import PlanAdmissionMode, PlanAdmissionService
from agent.planning.plan_model import Plan


def scoped_replan_observations(
    orchestrator: Any,
    candidate_steps: Plan | Sequence[Mapping[str, Any]],
) -> tuple[str | None, Sequence[Mapping[str, Any]]]:
    service = PlanAdmissionService(orchestrator)
    policy = service.policy_for(PlanAdmissionMode.REPLAN, candidate_steps)
    observations, plan_id = service.observation_scope(candidate_steps, policy)
    return plan_id, observations


__all__ = ["scoped_replan_observations"]

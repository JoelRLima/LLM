"""Typed plan recovery and validation-repair orchestration."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, List, Optional

from agent.planning.plan_model import Plan, ToolPlanStep
from agent.planning.provenance_validation import grounded_user_literal_narrowing
from agent.planning.replan_models import ReplanContext
from agent.planning.validation_repair_contracts import accepts_constrained_repair
from agent.runtime.failures import FailureFact
from agent.runtime.recovery import RecoveryScope


def replan_blocked_steps(
    gateway: Any,
    plan: Plan,
    objective: str,
    blocked_steps: List[Any],
    planning_context: Any = None,
    planning_view: Any = None,
    repair_budget: Mapping[str, int] | None = None,
) -> Optional[Plan]:
    del repair_budget
    return _replan_typed_plan(
        gateway, plan, objective, blocked_steps, planning_context, planning_view
    )


def _replan_typed_plan(
    gateway: Any,
    plan: Plan,
    objective: str,
    blocked_steps: List[Any],
    planning_context: Any,
    planning_view: Any,
) -> Plan | None:
    updated: Plan | None = plan
    allowed = {item.index for item in blocked_steps}
    for blocked in sorted(blocked_steps, key=lambda item: item.index, reverse=True):
        if updated is None:
            return None
        updated = _replace_typed_step(
            gateway,
            updated,
            objective,
            blocked,
            planning_context,
            planning_view,
            allowed,
        )
        if updated is None:
            return None
    return updated or None


def _replace_typed_step(
    gateway: Any,
    plan: Plan,
    objective: str,
    blocked: Any,
    planning_context: Any,
    planning_view: Any,
    allowed_blocked_indices: set[int],
) -> Plan | None:
    from agent.planning.replan import replan
    from agent.planning.validation_repair_support import _validate_typed_candidate
    from agent.runtime.logging import logger

    index = blocked.index
    if not 0 <= index < len(plan) or not blocked.is_validation_repair:
        logger.warning(
            "Passo %s rejeitado sem reparo de campo deterministico; abortando.",
            index + 1,
        )
        return None
    step = plan[index]
    if not isinstance(step, ToolPlanStep):
        return None
    if step.tool == "grep" and blocked.repairable_fields == frozenset({"pattern"}):
        literal = grounded_user_literal_narrowing(
            rejected_value=step.args.get("pattern"), objective=objective
        )
        if literal is not None:
            args = dict(step.args)
            args["pattern"] = literal
            bindings = dict(step.bindings or {})
            bindings.pop("pattern", None)
            candidate = Plan(
                (
                    *plan.steps[:index],
                    ToolPlanStep(step.step_id, step.tool, args, bindings or None),
                    *plan.steps[index + 1 :],
                )
            )
            accepted = _validate_typed_candidate(
                gateway,
                candidate,
                objective,
                index,
                planning_context,
                planning_view,
                allowed_blocked_indices,
            )
            if accepted is not None:
                gateway.orchestrator._emit(
                    "validation_repair",
                    {
                        "step": index,
                        "tool": "grep",
                        "field": "pattern",
                        "strategy": "deterministic_grounded_literal",
                        "source": "user_literal",
                    },
                )
                return accepted

    policy = getattr(gateway.orchestrator, "task_policy", None)
    budget = getattr(gateway.orchestrator.agent_state, "recovery_budget", None)
    allowed_repair = (
        policy.authorize_recovery(RecoveryScope.VALIDATION_REPAIRS).allowed
        if policy is not None
        else budget is None or budget.try_consume(RecoveryScope.VALIDATION_REPAIRS)
    )
    if not allowed_repair:
        logger.warning("Orcamento de reparo de validacao esgotado para o passo %s.", index + 1)
        return None
    context = ReplanContext(
        task=objective,
        current_step=step.to_dict(),
        tool_history=gateway.orchestrator.agent_state.tool_history,
        failure=FailureFact.from_code(
            "INVALID_ARGUMENTS",
            message=str(blocked.reason),
            tool_name=step.tool,
            step_id=step.step_id,
        ),
    )
    action = replan(
        context,
        context.failure,
        gateway.orchestrator,
        planning_context=planning_context,
        planning_view=planning_view,
        validation_repair=True,
        repairable_fields=tuple(sorted(blocked.repairable_fields)),
        prior_steps=tuple(
            (candidate_index + 1, candidate.to_dict())
            for candidate_index, candidate in enumerate(plan.steps[:index])
            if isinstance(candidate, ToolPlanStep)
        ),
    )
    gateway.orchestrator._emit(
        "replan",
        {
            "original_step": index,
            "error": blocked.reason,
            "strategy": action.source if action else "none",
            "replacement_steps": len(action.steps) if action else 0,
        },
    )
    if not action or len(action.steps) != 1:
        return None
    action_step = action.steps[0]
    if not isinstance(action_step, ToolPlanStep):
        return None
    replacement = ToolPlanStep(
        step_id=step.step_id,
        tool=action_step.tool,
        args=action_step.args,
        bindings=action_step.bindings,
    )
    candidate = Plan(
        (*plan.steps[:index], replacement, *plan.steps[index + 1 :])
    )
    if not accepts_constrained_repair(
        step.to_dict(), replacement.to_dict(), blocked.repairable_fields
    ):
        return None
    return _validate_typed_candidate(
        gateway,
        candidate,
        objective,
        index,
        planning_context,
        planning_view,
        allowed_blocked_indices,
    )


__all__ = ["replan_blocked_steps"]

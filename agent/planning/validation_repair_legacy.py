"""Legacy list-shaped repair adapter kept outside the typed repair core."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from functools import partial
from typing import Any, Dict, List

from agent.planning.grounded_repair import try_grounded_grep_repair
from agent.planning.plan_model import ToolPlanStep
from agent.planning.replan_models import ReplanContext
from agent.planning.validation_repair_contracts import accepts_constrained_repair
from agent.planning.validation_repair_support import (
    _validate_reintegrated_candidate,
)
from agent.runtime.failures import FailureFact
from agent.runtime.recovery import RecoveryScope


def replace_blocked_step(
    gateway: Any,
    plan: List[Dict[str, Any]],
    objective: str,
    blocked: Any,
    planning_context: Any = None,
    planning_view: Any = None,
    repair_budget: Mapping[str, int] | None = None,
    *,
    _allowed_blocked_indices: set[int] | None = None,
) -> bool:
    from agent.planning.replan import replan
    from agent.runtime.logging import logger

    del repair_budget
    index = blocked.index
    if index >= len(plan):
        return False
    step = plan[index] if isinstance(plan[index], dict) else {"tool": "", "args": {}}
    failure = FailureFact.from_code(
        "INVALID_ARGUMENTS",
        message=str(blocked.reason),
        tool_name=str(step.get("tool") or "") or None,
        step_id=str(step.get("_step_id")) if step.get("_step_id") else None,
    )
    context = ReplanContext(
        task=objective,
        current_step=step,
        tool_history=gateway.orchestrator.agent_state.tool_history,
        failure=failure,
    )
    if not blocked.is_validation_repair:
        logger.warning(
            "Passo %s rejeitado sem reparo de campo deterministico; abortando.",
            index + 1,
        )
        return False
    if _try_grounded_repair(
        gateway,
        plan,
        objective,
        blocked,
        planning_context,
        planning_view,
        _allowed_blocked_indices,
    ):
        return True
    policy = getattr(gateway.orchestrator, "task_policy", None)
    budget = getattr(gateway.orchestrator.agent_state, "recovery_budget", None)
    allowed_repair = (
        policy.authorize_recovery(RecoveryScope.VALIDATION_REPAIRS).allowed
        if policy is not None
        else budget is None or budget.try_consume(RecoveryScope.VALIDATION_REPAIRS)
    )
    if not allowed_repair:
        logger.warning(
            "Orcamento de reparo de validacao esgotado para o passo %s.", index + 1
        )
        return False
    prior_steps = tuple(
        (candidate_index + 1, candidate)
        for candidate_index, candidate in enumerate(plan[:index])
        if isinstance(candidate, dict) and isinstance(candidate.get("tool"), str)
    )
    action = replan(
        context,
        failure,
        gateway.orchestrator,
        planning_context=planning_context,
        planning_view=planning_view,
        validation_repair=True,
        repairable_fields=tuple(sorted(blocked.repairable_fields)),
        prior_steps=prior_steps,
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
    if not action or not action.steps or len(action.steps) != 1:
        logger.warning(
            "Passo %s permanece bloqueado: nenhuma substituicao valida.", index + 1
        )
        return False
    action_step = action.steps[0]
    if not isinstance(action_step, ToolPlanStep):
        logger.warning(
            "Passo %s permanece bloqueado: substituicao nao e tool.", index + 1
        )
        return False
    action_mapping = action_step.to_dict()
    if not accepts_constrained_repair(step, action_mapping, blocked.repairable_fields):
        logger.warning(
            "Passo %s permanece bloqueado: nenhuma substituicao valida.", index + 1
        )
        return False
    replacement = deepcopy(action_mapping)
    if "_step_id" in step:
        replacement["_step_id"] = step["_step_id"]
    candidate = (
        [deepcopy(item) for item in plan[:index]]
        + [replacement]
        + [deepcopy(item) for item in plan[index + 1 :]]
    )
    accepted = _validate_reintegrated_candidate(
        gateway,
        candidate,
        objective,
        index,
        planning_context,
        planning_view,
        _allowed_blocked_indices or {index},
    )
    if accepted is None:
        logger.warning(
            "Reparo do passo %s rejeitado: candidate causal completo invalido.",
            index + 1,
        )
        return False
    plan[:] = accepted
    logger.info("Passo %s substituido atomicamente no plano causal.", index + 1)
    return True


def _try_grounded_repair(
    gateway: Any,
    plan: List[Dict[str, Any]],
    objective: str,
    blocked: Any,
    planning_context: Any,
    planning_view: Any,
    allowed: set[int] | None,
) -> bool:
    from agent.runtime.logging import logger

    index = blocked.index
    step = plan[index]
    if step.get("tool") != "grep" or blocked.repairable_fields != frozenset({"pattern"}):
        return False
    repaired = try_grounded_grep_repair(
        plan,
        objective,
        index,
        blocked.repairable_fields,
        accepts_constrained_repair,
        partial(
            _validate_reintegrated_candidate,
            gateway,
            objective=objective,
            repaired_index=index,
            planning_context=planning_context,
            planning_view=planning_view,
            allowed_blocked_indices=allowed or {index},
        ),
    )
    if not repaired:
        return False
    logger.info(
        "Passo %s reparado por narrowing deterministico de literal grounded.", index + 1
    )
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
    return True


__all__ = ["replace_blocked_step"]

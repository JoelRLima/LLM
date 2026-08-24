"""Small, field-scoped contracts for deterministic validation repair."""

from __future__ import annotations

import re
from collections.abc import Mapping
from copy import deepcopy
from functools import partial
from typing import Any, Dict, List, Optional

from agent.planning.grounded_repair import try_grounded_grep_repair


def repairable_fields(step: Any, problem: str) -> frozenset[str]:
    if not isinstance(step, Mapping):
        return frozenset()
    patterns = (
        r"Argumento ['\"]([^'\"]+)['\"] requer proveniencia",
        r"Campo ['\"]([^'\"]+)['\"] obrigat",
        r"Campo .*ausente:\s*['\"]([^'\"]+)['\"]",
        r"missing required argument:\s*([A-Za-z0-9_.-]+)",
        r"argument ['\"]([^'\"]+)['\"]",
        r"'([A-Za-z0-9_.-]+)':\s*(?:esperado|valor)",
    )
    text = str(problem or "")
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return frozenset({match.group(1)})
    return frozenset()


def accepts_constrained_repair(
    original: Mapping[str, Any], candidate: Mapping[str, Any], fields: frozenset[str]
) -> bool:
    if not fields or not isinstance(candidate, Mapping):
        return False
    if any(key not in {"tool", "args", "bindings", "_step_id"} for key in candidate):
        return False
    if candidate.get("tool") != original.get("tool"):
        return False
    if "_step_id" in original and candidate.get("_step_id") not in (None, original.get("_step_id")):
        return False
    original_args = original.get("args")
    candidate_args = candidate.get("args")
    if not isinstance(original_args, Mapping) or not isinstance(candidate_args, Mapping):
        return False
    frozen = set(str(key) for key in original_args) - set(fields)
    if any(key not in candidate_args or candidate_args[key] != original_args[key] for key in frozen):
        return False
    if any(str(key) not in set(original_args) | set(fields) for key in candidate_args):
        return False
    original_bindings = original.get("bindings")
    candidate_bindings = candidate.get("bindings")
    original_bindings = original_bindings if isinstance(original_bindings, Mapping) else {}
    candidate_bindings = candidate_bindings if isinstance(candidate_bindings, Mapping) else {}
    for key, value in candidate_bindings.items():
        if str(key) not in fields and (key not in original_bindings or original_bindings[key] != value):
            return False
    return all(
        key in candidate_bindings and candidate_bindings[key] == value
        for key, value in original_bindings.items()
        if str(key) not in fields
    )


def replan_blocked_steps(
    gateway: Any,
    plan: List[Dict[str, Any]],
    objective: str,
    blocked_steps: List[Any],
    planning_context: Any = None,
    planning_view: Any = None,
    repair_budget: Dict[str, int] | None = None,
) -> Optional[List[Dict[str, Any]]]:
    updated = list(plan)
    allowed_blocked_indices = {item.index for item in blocked_steps}
    for blocked in sorted(blocked_steps, key=lambda item: item.index, reverse=True):
        if not replace_blocked_step(
            gateway,
            updated,
            objective,
            blocked,
            planning_context,
            planning_view,
            repair_budget,
            _allowed_blocked_indices=allowed_blocked_indices,
        ):
            return None
    return updated or None


def replace_blocked_step(
    gateway: Any,
    plan: List[Dict[str, Any]],
    objective: str,
    blocked: Any,
    planning_context: Any = None,
    planning_view: Any = None,
    repair_budget: Dict[str, int] | None = None,
    *,
    _allowed_blocked_indices: set[int] | None = None,
) -> bool:
    from agent.planning.replan import ReplanContext, replan
    from agent.runtime.logging import logger

    index = blocked.index
    if index >= len(plan):
        return False
    step = plan[index] if isinstance(plan[index], dict) else {"tool": "", "args": {}}
    context = ReplanContext(
        task=objective,
        current_step=step,
        tool_history=gateway.orchestrator.agent_state.tool_history,
        last_exception=blocked.reason,
    )
    if not blocked.is_validation_repair:
        logger.warning(
            "Passo %s rejeitado sem reparo de campo deterministico; abortando.", index + 1
        )
        return False
    if (
        step.get("tool") == "grep"
        and blocked.repairable_fields == frozenset({"pattern"})
        and try_grounded_grep_repair(
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
                allowed_blocked_indices=_allowed_blocked_indices or {index},
            ),
        )
    ):
        logger.info(
            "Passo %s reparado por narrowing determinístico de literal grounded.",
            index + 1,
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
    if repair_budget is not None and repair_budget.get("remaining", 0) <= 0:
        logger.warning("Orcamento de reparo de validacao esgotado para o passo %s.", index + 1)
        return False
    if repair_budget is not None:
        repair_budget["remaining"] = repair_budget.get("remaining", 0) - 1
    repair_error = (
        "deterministic validation rejected argument field(s): "
        + ", ".join(sorted(blocked.repairable_fields))
        + "; validator detail: "
        + str(blocked.reason)[:256]
    )
    prior_steps = tuple(
        (candidate_index + 1, candidate)
        for candidate_index, candidate in enumerate(plan[:index])
        if isinstance(candidate, dict) and isinstance(candidate.get("tool"), str)
    )
    action = replan(
        context,
        repair_error,
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
        logger.warning("Passo %s permanece bloqueado: nenhuma substituicao valida.", index + 1)
        return False
    if not accepts_constrained_repair(step, action.steps[0], blocked.repairable_fields):
        logger.warning("Passo %s permanece bloqueado: nenhuma substituicao valida.", index + 1)
        return False
    replacement = deepcopy(action.steps[0])
    if "_step_id" in step:
        replacement["_step_id"] = step["_step_id"]
    candidate = [deepcopy(item) for item in plan[:index]] + [replacement] + [deepcopy(item) for item in plan[index + 1 :]]
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
            "Reparo do passo %s rejeitado: candidate causal completo inválido.", index + 1
        )
        return False
    plan[:] = accepted
    logger.info("Passo %s substituido atomicamente no plano causal.", index + 1)
    return True


def _validate_reintegrated_candidate(
    gateway: Any,
    candidate: List[Dict[str, Any]],
    objective: str,
    repaired_index: int,
    planning_context: Any,
    planning_view: Any,
    allowed_blocked_indices: set[int],
) -> Optional[List[Dict[str, Any]]]:
    from agent.planning.plan_validator import PlanValidator
    from agent.planning.presentation import validate_planning_view_binding
    from agent.runtime.logging import logger

    context = planning_context or getattr(gateway.orchestrator, "planning_context", None)
    presentation = planning_view
    if context is not None and presentation is not None:
        validate_planning_view_binding(context, presentation, "linear")
    elif context is not None and callable(getattr(gateway, "_planning_view", None)):
        presentation = gateway._planning_view(context, "linear")

    canonical = _has_deferred_or_result_bindings(candidate)
    prepared = candidate
    if canonical:
        binder = getattr(gateway, "_bind_deferred_references", None)
        if not callable(binder):
            return None
        try:
            prepared = binder(candidate)
        except (TypeError, ValueError, KeyError) as exc:
            logger.warning("Candidate de reparo não pôde ser canonicalizado: %s", exc)
            return None

    state = getattr(gateway.orchestrator, "agent_state", None)
    current_ids = {
        str(step.get("_step_id"))
        for step in getattr(state, "plan", ())
        if isinstance(step, Mapping) and step.get("_step_id")
    }
    candidate_ids = {
        str(step.get("_step_id"))
        for step in prepared
        if isinstance(step, Mapping) and step.get("_step_id")
    }
    scoped_plan_id = getattr(state, "plan_identity", None)
    if not scoped_plan_id or not current_ids.intersection(candidate_ids):
        scoped_plan_id = None
        scoped_observations = ()
    else:
        scoped_observations = getattr(state, "tool_history", ())
    validator = PlanValidator(
        getattr(gateway.orchestrator, "skills", {}) or {},
        getattr(gateway.orchestrator, "active_skills", []) or [],
        getattr(gateway.orchestrator, "allowed_capabilities", None),
        getattr(gateway.orchestrator, "tool_registry", None),
        planning_context=context,
        presented_names=presentation.presented_names if presentation is not None else None,
        planning_view=presentation,
        objective=objective,
        canonical_deferred_references=canonical,
        available_observations=scoped_observations,
        plan_identity=scoped_plan_id,
    )
    report = validator.validate(prepared)
    for error in report.errors:
        logger.warning("[VALIDATOR][validation repair] %s", error)
    blocked_indexes = {item.index for item in report.blocked_steps}
    if report.errors or repaired_index in blocked_indexes:
        return None
    if blocked_indexes - (allowed_blocked_indices - {repaired_index}):
        return None
    return [dict(step) for step in prepared]


def _has_deferred_or_result_bindings(plan: List[Dict[str, Any]]) -> bool:
    return any(isinstance(step, Mapping) and (step.get("kind") == "deferred_condition" or "bindings" in step) for step in plan)


__all__ = ["accepts_constrained_repair", "repairable_fields", "replan_blocked_steps", "replace_blocked_step"]

"""Bounded LLM continuation for failed structured requirement checks."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from llm_agent.agent.planning.plan_builder import PlanningDecisionKind
from llm_agent.agent.planning.plan_model import Plan, serialize_plan
from llm_agent.agent.planning.reasoning_boundary import (
    BoundaryContinuationResult,
    call_extension_boundary,
)
from llm_agent.agent.planning.task_completion import review_task_completion
from llm_agent.agent.planning.task_terminal import mark_terminal_blocked
from llm_agent.agent.runtime.budget import BudgetExhausted
from llm_agent.agent.runtime.recovery import RecoveryScope

_REASONS = frozenset({
    "REQUIREMENT_VERIFICATION_FAILED",
    "REQUIREMENT_VERIFICATION_INCONCLUSIVE",
})


def continue_failed_requirements(
    orchestrator: Any, objective: str
) -> BoundaryContinuationResult | None:
    """Attempt one validated plan extension for canonical verification blockers."""
    review = review_task_completion(orchestrator)
    if review.accepted or review.reason_code not in _REASONS:
        return None
    blocked = _admit_attempt(orchestrator, review.requirement_verification or {})
    if blocked is not None:
        return blocked
    return _propose_and_extend(orchestrator, objective)


def _admit_attempt(
    orchestrator: Any, document: dict[str, Any]
) -> BoundaryContinuationResult | None:
    state = orchestrator.agent_state
    fingerprint = _fingerprint(document)
    fingerprints = tuple(getattr(state, "requirement_replan_fingerprints", ()))
    if not fingerprint or fingerprint in fingerprints:
        return _blocked(orchestrator, "requirement_replan_no_progress", "Nenhum progresso nas condições estruturadas foi observado.")
    if len(fingerprints) >= 64:
        return _blocked(orchestrator, "requirement_replan_history_full", "O histórico limitado de tentativas está cheio.")
    authorize = getattr(getattr(orchestrator, "task_policy", None), "authorize_recovery", None)
    budget = getattr(state, "recovery_budget", None)
    if callable(authorize):
        if not authorize(RecoveryScope.LLM_REPLANS).allowed:
            return _blocked(orchestrator, "requirement_replan_budget_exhausted", "O orçamento de replanejamento foi esgotado.")
    elif budget is None or not budget.try_consume(RecoveryScope.LLM_REPLANS):
        return _blocked(orchestrator, "requirement_replan_budget_unavailable", "Não há orçamento disponível para replanejar os requisitos.")
    state.requirement_replan_fingerprints = (*fingerprints, fingerprint)
    return None


def _propose_and_extend(orchestrator: Any, objective: str) -> BoundaryContinuationResult:
    continuation = _request(orchestrator, objective)
    if continuation is None or continuation.kind is not PlanningDecisionKind.EXECUTE or not continuation.plan:
        return _blocked(orchestrator, "requirement_replan_invalid", "A correção proposta não forneceu um plano executável.")
    plan = serialize_plan(continuation.plan) if isinstance(continuation.plan, Plan) else continuation.plan
    orchestrator._emit("continuation_plan_proposed", {
        "steps": len(continuation.plan), "plan": plan, "source": "requirement_verification",
    })
    extender = getattr(getattr(orchestrator, "execution_gateway", None), "extend_validated_plan", None)
    if not callable(extender):
        return _blocked(orchestrator, "requirement_replan_invalid", "Não foi possível validar o plano de correção.")
    try:
        validated = call_extension_boundary(
            extender, continuation.plan, objective, getattr(continuation, "planning_view", None)
        )
    except BudgetExhausted:
        raise
    except Exception:
        validated = None
    if validated is None:
        return _blocked(orchestrator, "requirement_replan_invalid", "Não foi possível validar o plano de correção.")
    return BoundaryContinuationResult(extended=True, planning_view=getattr(continuation, "planning_view", None))


def _request(orchestrator: Any, objective: str) -> Any | None:
    builder = getattr(orchestrator, "plan_builder", None)
    request = getattr(builder, "continue_after_reasoning_boundary", None)
    if not callable(request):
        return None
    try:
        return request(objective)
    except BudgetExhausted:
        raise
    except Exception:
        return None


def _fingerprint(document: dict[str, Any]) -> str:
    checks = document.get("checks", ())
    failed = sorted(
        (row.get("check_id"), row.get("requirement_id"), row.get("status"), row.get("reason_code"))
        for row in checks
        if isinstance(row, dict) and row.get("status") != "satisfied"
    )
    definition = document.get("definition", {})
    stable_definition = tuple(
        definition.get(key)
        for key in (
            "task_id", "active_phase_id", "contract_version", "contract_digest",
            "spec_version", "spec_digest",
        )
    ) if isinstance(definition, dict) else ()
    if not failed:
        return ""
    payload = json.dumps((stable_definition, failed), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _blocked(orchestrator: Any, reason: str, detail: str) -> BoundaryContinuationResult:
    answer = mark_terminal_blocked(
        orchestrator, reason_code=reason, message=f"A verificação dos requisitos continua bloqueada: {detail}"
    )
    return BoundaryContinuationResult(answer=answer, blocked=True)


__all__ = ["continue_failed_requirements"]

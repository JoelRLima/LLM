"""Canonical task-level completion policy for the linear execution path."""

from __future__ import annotations

import re
from enum import Enum
from typing import Any

from agent.planning.completion_observations import (
    eligible_waiver_observations,
    observation_references,
    publish_outcome,
    refresh_executed_effects,
    terminal_failure,
)
from agent.planning.operational_constants import TERMINAL_FAILURE_STATUSES
from agent.planning.plan_builder import PlanningDecisionKind
from agent.planning.reasoning_boundary import BoundaryContinuationResult
from agent.planning.reasoning_boundary import (
    continue_after_reasoning_boundary as _reasoning_boundary,
)
from agent.reporting.operational_outcome import PUBLIC_TERMINAL_STATUSES


class CompletionDisposition(str, Enum):
    COMPLETE = "complete"
    BLOCK = "block"
    FAIL = "fail"


MAX_CONTINUATION_ATTEMPTS = 1
_NON_SUCCESS_STATUSES = PUBLIC_TERMINAL_STATUSES - {"succeeded"}


_EFFECT_TERMS = frozenset(
    "adicione adicionar ajuste ajustar altere alterar change corrija corrigir create crie criar delete edit edite editar escreva escrever fix modifique modificar modify refactor remova remover replace substitua substituir update write".split()
)
_DIRECT_TEXT_REQUEST = re.compile(r"\b(?:escreva|write)\s+exatamente\b", re.IGNORECASE)


def initialize_task_progression(orchestrator: Any, objective: str) -> None:
    terms = set(re.findall(r"\w+", objective.casefold()))
    requested = ("write",) if terms & _EFFECT_TERMS and not _DIRECT_TEXT_REQUEST.search(objective) else ()
    orchestrator.agent_state.reset_task_progression(requested)
    orchestrator.agent_state.reasoning_last_history_count = len(
        orchestrator.agent_state.tool_history
    )


def bind_effect_waiver(orchestrator: Any, observation_index: int, *, effects: tuple[str, ...] | None = None, source: str = "continuation") -> bool:
    match = next((item for index, item in eligible_waiver_observations(orchestrator) if index == observation_index), None)
    pending = orchestrator.agent_state.pending_effects()
    if match is None or not pending:
        return False
    selected = pending if effects is None else effects
    if not selected or any(effect not in pending for effect in selected):
        return False
    for effect in selected:
        orchestrator.agent_state.waive_effect(effect)
    orchestrator._emit("effect_waiver_bound", {"effects": list(selected), "observation_index": observation_index, "invocation_id": match.get("invocation_id"), "source": source})
    return True


def needs_effect_continuation(orchestrator: Any, objective: str) -> bool:
    del objective
    refresh_executed_effects(orchestrator)
    state = orchestrator.agent_state
    return not terminal_failure(orchestrator) and bool(state.pending_effects()) and state.continuation_attempts < MAX_CONTINUATION_ATTEMPTS


def mark_unfinished_effect(orchestrator: Any, objective: str) -> str:
    state = orchestrator.agent_state
    if state.terminal_disposition not in (None, CompletionDisposition.COMPLETE.value):
        return _terminal_message(state)
    message = "A tarefa não foi concluída: o efeito solicitado permanece pendente."
    state = orchestrator.agent_state
    state.terminal_disposition = CompletionDisposition.BLOCK.value
    state.project_last_result("planner", {}, {"ok": False, "done": True, "status": "blocked", "executed": False, "error": "requested_effect_pending", "error_code": "requested_effect_pending", "message": message})
    orchestrator._emit("task_blocked", {"reason": "requested_effect_pending", "objective": objective})
    publish_outcome(orchestrator)
    return message


def mark_reasoning_boundary_blocked(orchestrator: Any, objective: str) -> str:
    state = orchestrator.agent_state
    if state.terminal_disposition is not None:
        return _terminal_message(state)
    message = "A tarefa não pôde prosseguir após a fronteira de raciocínio."
    state = orchestrator.agent_state
    state.terminal_disposition = CompletionDisposition.BLOCK.value
    state.project_last_result("planner", {}, {"ok": False, "done": True, "status": "blocked", "executed": False, "error": "reasoning_boundary_blocked", "error_code": "reasoning_boundary_blocked", "message": message})
    orchestrator._emit("task_blocked", {"reason": "reasoning_boundary_blocked", "objective": objective})
    publish_outcome(orchestrator)
    return message


def continue_after_reasoning_boundary(orchestrator: Any, objective: str) -> BoundaryContinuationResult:
    """Translate a pure reasoning decision through canonical completion."""

    boundary = _reasoning_boundary(orchestrator, objective)
    if boundary.blocked:
        return BoundaryContinuationResult(
            answer=mark_reasoning_boundary_blocked(orchestrator, objective),
            blocked=True,
        )
    if boundary.completed:
        blocker = allow_linear_completion(orchestrator, objective)
        return BoundaryContinuationResult(answer=blocker, completed=blocker is None)
    return boundary


def mark_terminal_failure(orchestrator: Any) -> None:
    state = orchestrator.agent_state
    existing = getattr(state, "terminal_disposition", None)
    if existing == "succeeded":
        existing = None
        state.terminal_disposition = None
    if existing not in (None, CompletionDisposition.COMPLETE.value):
        return
    result = getattr(state, "last_result", None)
    status = str(result.get("status") or "") if isinstance(result, dict) else ""
    if status == "blocked":
        state.terminal_disposition = CompletionDisposition.BLOCK.value
    elif status == "permission_denied":
        state.terminal_disposition = "permission_denied"
    elif status == "failed":
        state.terminal_disposition = CompletionDisposition.FAIL.value
    elif status in _NON_SUCCESS_STATUSES:
        state.terminal_disposition = status
    else:
        state.terminal_disposition = CompletionDisposition.FAIL.value


def mark_terminal_blocked(
    orchestrator: Any,
    *,
    reason_code: str,
    message: str,
    status: str = CompletionDisposition.BLOCK.value,
) -> str:
    """Establish a non-executable terminal boundary with machine-readable evidence."""

    refresh_executed_effects(orchestrator)
    state = orchestrator.agent_state
    if getattr(state, "terminal_disposition", None) == "succeeded":
        state.terminal_disposition = None
    if getattr(state, "terminal_disposition", None) in (None, CompletionDisposition.COMPLETE.value):
        state.terminal_disposition = (
            status if status in _NON_SUCCESS_STATUSES else CompletionDisposition.BLOCK.value
        )
        project = getattr(state, "project_last_result", None)
        if callable(project):
            project(
                "planner",
                {},
                {
                    "ok": False,
                    "done": True,
                    "status": state.terminal_disposition,
                    "executed": False,
                    "error": reason_code,
                    "error_code": reason_code,
                    "message": message,
                },
            )
    publish_outcome(orchestrator)
    return _terminal_message(state) or message


def mark_terminal_cancelled(
    orchestrator: Any,
    message: str = "Tarefa cancelada pelo usuario.",
) -> str:
    """Record cancellation through the same canonical terminal boundary."""

    refresh_executed_effects(orchestrator)
    state = orchestrator.agent_state
    if getattr(state, "terminal_disposition", None) == CompletionDisposition.COMPLETE.value:
        orchestrator._cancelled = False
        return message
    orchestrator._cancelled = True
    state.terminal_disposition = "cancelled"
    project = getattr(state, "project_last_result", None)
    if callable(project):
        project(
            "orchestrator",
            {},
            {
                "ok": False,
                "done": True,
                "status": "cancelled",
                "executed": False,
                "error": "CANCELLED",
                "error_code": "CANCELLED",
                "message": message,
            },
        )
    publish_outcome(orchestrator)
    return _terminal_message(state) or message


def _terminal_message(state: Any) -> str:
    disposition = getattr(state, "terminal_disposition", None)
    if disposition == CompletionDisposition.COMPLETE.value:
        return ""
    result = getattr(state, "last_result", None)
    if isinstance(result, dict):
        error = result.get("error")
        message = result.get("message")
        if (
            result.get("error_code") == error
            and isinstance(message, str)
            and message.strip()
        ):
            return message.strip()
        message = error or message
        if isinstance(message, str) and message.strip():
            return message.strip()
    if disposition == CompletionDisposition.BLOCK.value:
        return "A tarefa foi bloqueada antes de concluir todos os efeitos."
    if disposition == CompletionDisposition.FAIL.value:
        return "A tarefa nÃ£o pÃ´de ser concluÃ­da."
    if disposition == "cancelled":
        return "Tarefa cancelada pelo usuario."
    if disposition in _NON_SUCCESS_STATUSES:
        return f"A tarefa terminou com status operacional: {disposition}."
    return ""


def allow_linear_completion(orchestrator: Any, objective: str) -> str | None:
    existing = getattr(orchestrator.agent_state, "terminal_disposition", None)
    if existing == "succeeded":
        orchestrator.agent_state.terminal_disposition = None
        existing = None
    refresh_executed_effects(orchestrator)
    if getattr(orchestrator, "_cancelled", False):
        mark_terminal_cancelled(orchestrator)
        return _terminal_message(orchestrator.agent_state) or "Tarefa cancelada pelo usuario."
    if terminal_failure(orchestrator):
        mark_terminal_failure(orchestrator)
        publish_outcome(orchestrator)
        result = getattr(orchestrator.agent_state, "last_result", None)
        if isinstance(result, dict) and str(result.get("status") or "") in TERMINAL_FAILURE_STATUSES:
            return _terminal_message(orchestrator.agent_state) or "A tarefa não pôde ser concluída."
        return "A tarefa não pôde ser concluída."
    if existing is not None and existing != CompletionDisposition.COMPLETE.value:
        return _terminal_message(orchestrator.agent_state) or None
    pending = getattr(orchestrator.agent_state, "pending_effects", lambda: ())()
    if pending:
        return mark_unfinished_effect(orchestrator, objective)
    if existing == CompletionDisposition.COMPLETE.value:
        return None
    orchestrator.agent_state.terminal_disposition = CompletionDisposition.COMPLETE.value
    publish_outcome(orchestrator)
    return None


def complete_direct_answer(orchestrator: Any, objective: str, answer: str) -> str:
    return allow_linear_completion(orchestrator, objective) or answer


def continue_after_observation(orchestrator: Any, objective: str) -> str | None:
    state = orchestrator.agent_state
    state.continuation_attempts += 1
    refresh_executed_effects(orchestrator)
    executed = ", ".join(state.executed_effects) or "nenhum efeito de escrita executado"
    try:
        continuation = orchestrator.plan_builder.continue_after_observation(objective, executed, observation_references(orchestrator))
    except Exception:
        return mark_unfinished_effect(orchestrator, objective)
    if continuation.kind is PlanningDecisionKind.COMPLETE:
        index = continuation.waiver_observation_index
        if index is None or not bind_effect_waiver(orchestrator, index):
            return mark_unfinished_effect(orchestrator, objective)
        return allow_linear_completion(orchestrator, objective)
    if continuation.kind is not PlanningDecisionKind.EXECUTE or not continuation.plan:
        return mark_unfinished_effect(orchestrator, objective)
    orchestrator._emit("continuation_plan_proposed", {"steps": len(continuation.plan), "plan": continuation.plan})
    try:
        validated = orchestrator.execution_gateway.extend_validated_plan(continuation.plan, objective)
    except Exception:
        validated = None
    return None if validated is not None else mark_unfinished_effect(orchestrator, objective)


__all__ = [
    "CompletionDisposition", "BoundaryContinuationResult", "MAX_CONTINUATION_ATTEMPTS",
    "allow_linear_completion", "bind_effect_waiver", "complete_direct_answer",
    "continue_after_observation", "continue_after_reasoning_boundary", "initialize_task_progression",
    "mark_terminal_failure", "mark_terminal_blocked", "mark_terminal_cancelled",
    "mark_unfinished_effect", "mark_reasoning_boundary_blocked",
    "needs_effect_continuation", "refresh_executed_effects",
]

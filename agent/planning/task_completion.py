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


class CompletionDisposition(str, Enum):
    COMPLETE = "complete"
    BLOCK = "block"
    FAIL = "fail"


_EFFECT_TERMS = frozenset(
    {
        "adicione",
        "adicionar",
        "ajuste",
        "ajustar",
        "altere",
        "alterar",
        "change",
        "corrija",
        "corrigir",
        "create",
        "crie",
        "criar",
        "delete",
        "edit",
        "edite",
        "editar",
        "escreva",
        "escrever",
        "fix",
        "modifique",
        "modificar",
        "modify",
        "refactor",
        "remova",
        "remover",
        "replace",
        "substitua",
        "substituir",
        "update",
        "write",
    }
)
_DIRECT_TEXT_REQUEST = re.compile(r"\b(?:escreva|write)\s+exatamente\b", re.IGNORECASE)


def initialize_task_progression(orchestrator: Any, objective: str) -> None:
    normalized_terms = set(re.findall(r"\w+", objective.casefold()))
    requests_workspace_effect = bool(normalized_terms & _EFFECT_TERMS) and not (
        _DIRECT_TEXT_REQUEST.search(objective)
    )
    requested = ("write",) if requests_workspace_effect else ()
    orchestrator.agent_state.reset_task_progression(requested)


def bind_effect_waiver(
    orchestrator: Any,
    observation_index: int,
    *,
    effects: tuple[str, ...] | None = None,
    source: str = "continuation",
) -> bool:
    match = next(
        (
            item
            for index, item in eligible_waiver_observations(orchestrator)
            if index == observation_index
        ),
        None,
    )
    if match is None:
        return False
    state = orchestrator.agent_state
    pending = state.pending_effects()
    if not pending:
        return False
    selected = pending if effects is None else effects
    if not selected or any(effect not in pending for effect in selected):
        return False
    for effect in selected:
        state.waive_effect(effect)
    orchestrator._emit(
        "effect_waiver_bound",
        {
            "effects": list(selected),
            "observation_index": observation_index,
            "invocation_id": match.get("invocation_id"),
            "source": source,
        },
    )
    return True


def needs_effect_continuation(orchestrator: Any, objective: str) -> bool:
    del objective
    refresh_executed_effects(orchestrator)
    state = orchestrator.agent_state
    return (
        not terminal_failure(orchestrator)
        and bool(state.pending_effects())
        and state.continuation_attempts < 1
    )


def mark_unfinished_effect(orchestrator: Any, objective: str) -> str:
    message = "A tarefa não foi concluída: o efeito solicitado permanece pendente."
    state = orchestrator.agent_state
    state.terminal_disposition = CompletionDisposition.BLOCK.value
    state.project_last_result(
        "planner",
        {},
        {
            "ok": False,
            "done": True,
            "status": "blocked",
            "executed": False,
            "error": "requested_effect_pending",
            "message": message,
        },
    )
    orchestrator._emit(
        "task_blocked", {"reason": "requested_effect_pending", "objective": objective}
    )
    publish_outcome(orchestrator)
    return message


def mark_terminal_failure(orchestrator: Any) -> None:
    state = orchestrator.agent_state
    if state.terminal_disposition is None:
        result = state.last_result
        status = str(result.get("status") or "") if isinstance(result, dict) else ""
        disposition = (
            CompletionDisposition.BLOCK
            if status in {"blocked", "permission_denied"}
            else CompletionDisposition.FAIL
        )
        state.terminal_disposition = disposition.value


def allow_linear_completion(orchestrator: Any, objective: str) -> str | None:
    """Return a truthful blocker, or authorize final synthesis/return."""

    refresh_executed_effects(orchestrator)
    if terminal_failure(orchestrator):
        mark_terminal_failure(orchestrator)
        publish_outcome(orchestrator)
        result = orchestrator.agent_state.last_result
        if isinstance(result, dict):
            status = str(result.get("status") or "")
            if status in TERMINAL_FAILURE_STATUSES:
                message = result.get("error") or result.get("message")
                if isinstance(message, str) and message.strip():
                    return message.strip()
        return "A tarefa não pôde ser concluída."
    if orchestrator.agent_state.pending_effects():
        return mark_unfinished_effect(orchestrator, objective)
    orchestrator.agent_state.terminal_disposition = CompletionDisposition.COMPLETE.value
    publish_outcome(orchestrator)
    return None


def complete_direct_answer(
    orchestrator: Any,
    objective: str,
    answer: str,
) -> str:
    blocker = allow_linear_completion(orchestrator, objective)
    return blocker or answer


def continue_after_observation(orchestrator: Any, objective: str) -> str | None:
    state = orchestrator.agent_state
    state.continuation_attempts += 1
    refresh_executed_effects(orchestrator)
    executed = ", ".join(state.executed_effects) or "nenhum efeito de escrita executado"
    continuation = orchestrator.plan_builder.continue_after_observation(
        objective,
        executed,
        observation_references(orchestrator),
    )
    if continuation.kind is PlanningDecisionKind.COMPLETE:
        index = continuation.waiver_observation_index
        if index is None or not bind_effect_waiver(orchestrator, index):
            return mark_unfinished_effect(orchestrator, objective)
        return allow_linear_completion(orchestrator, objective)
    if continuation.kind is PlanningDecisionKind.BLOCK:
        return mark_unfinished_effect(orchestrator, objective)
    if continuation.kind is not PlanningDecisionKind.EXECUTE or not continuation.plan:
        return mark_unfinished_effect(orchestrator, objective)

    orchestrator._emit(
        "continuation_plan_proposed",
        {"steps": len(continuation.plan), "plan": continuation.plan},
    )
    validated = orchestrator.execution_gateway.extend_validated_plan(
        continuation.plan,
        objective,
    )
    if validated is None:
        return mark_unfinished_effect(orchestrator, objective)
    return None


__all__ = [
    "CompletionDisposition",
    "allow_linear_completion",
    "bind_effect_waiver",
    "complete_direct_answer",
    "continue_after_observation",
    "initialize_task_progression",
    "mark_terminal_failure",
    "mark_unfinished_effect",
    "needs_effect_continuation",
    "refresh_executed_effects",
]

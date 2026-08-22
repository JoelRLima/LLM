"""Dispatch completion-review reasons to canonical terminal operations."""

from __future__ import annotations

from typing import Any, Callable

from agent.planning.completion_observations import publish_outcome
from agent.planning.operational_constants import TERMINAL_FAILURE_STATUSES
from agent.planning.task_completion_types import CompletionDisposition
from agent.planning.task_terminal import (
    _set_terminal,
    _terminal_message,
    mark_terminal_blocked,
    mark_terminal_cancelled,
    mark_terminal_failure,
    mark_unfinished_effect,
    mark_unfinished_obligation,
)


def accept_review(orchestrator: Any, existing: str | None) -> str | None:
    if existing != CompletionDisposition.COMPLETE.value:
        _set_terminal(orchestrator.agent_state, CompletionDisposition.COMPLETE.value)
        publish_outcome(orchestrator)
    return None


def _terminal_failure_review(orchestrator: Any) -> str:
    mark_terminal_failure(orchestrator)
    publish_outcome(orchestrator)
    result = getattr(orchestrator.agent_state, "last_result", None)
    if isinstance(result, dict) and str(result.get("status") or "") in TERMINAL_FAILURE_STATUSES:
        return _terminal_message(orchestrator.agent_state) or "A tarefa não pôde ser concluída."
    return "A tarefa não pôde ser concluída."


def reject_review(orchestrator: Any, objective: str, review: Any) -> str | None:
    handlers: dict[str, Callable[[], str | None]] = {
        "cancelled": lambda: mark_terminal_cancelled(orchestrator)
        or _terminal_message(orchestrator.agent_state)
        or "Tarefa cancelada pelo usuario.",
        "terminal_failure": lambda: _terminal_failure_review(orchestrator),
        "prohibited_effect_occurred": lambda: mark_terminal_blocked(
            orchestrator,
            reason_code="prohibited_effect_occurred",
            message="A tarefa foi bloqueada: ocorreu um efeito proibido.",
        ),
        "existing_terminal": lambda: _terminal_message(orchestrator.agent_state) or None,
        "obligation_evidence_missing": lambda: mark_terminal_blocked(
            orchestrator,
            reason_code="obligation_evidence_missing",
            message="A tarefa foi bloqueada: falta evidencia canonica para uma obrigacao terminal.",
        ),
        "requested_effect_pending": lambda: mark_unfinished_effect(orchestrator, objective),
        "task_obligation_pending": lambda: mark_unfinished_obligation(orchestrator, objective),
        "task_obligation_blocked": lambda: mark_terminal_blocked(
            orchestrator,
            reason_code="task_obligation_blocked",
            message="A tarefa foi bloqueada por uma obrigacao canonica.",
        ),
    }
    handler = handlers.get(getattr(review, "reason_code", None) or "")
    if handler is not None:
        return handler()
    return mark_terminal_blocked(
        orchestrator,
        reason_code=getattr(review, "reason_code", None) or "completion_review_failed",
        message="A tarefa nao passou pela revisao canonica de conclusao.",
    )


__all__ = ["accept_review", "reject_review"]

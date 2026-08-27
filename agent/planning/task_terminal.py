"""Canonical terminal-boundary mutations for task completion."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent.planning.completion_observations import (
    publish_outcome,
    refresh_executed_effects,
)
from agent.planning.task_completion_types import CompletionDisposition
from agent.runtime.outcome_taxonomy import (
    NON_SUCCESS_STATUSES,
    OperationalStatus,
    operational_status_for,
)

_NON_SUCCESS_STATUSES = NON_SUCCESS_STATUSES


def _set_terminal(state: Any, value: str | None) -> None:
    setter = getattr(state, "set_terminal_disposition", None)
    if callable(setter):
        setter(value)
    else:
        state.terminal_disposition = value


def mark_unfinished_effect(orchestrator: Any, objective: str) -> str:
    state = orchestrator.agent_state
    if state.terminal_disposition not in (None, CompletionDisposition.COMPLETE.value):
        return _terminal_message(state)
    message = "A tarefa não foi concluída: o efeito solicitado permanece pendente."
    _set_terminal(state, CompletionDisposition.BLOCK.value)
    state.project_last_result(
        "planner",
        {},
        {
            "ok": False,
            "done": True,
            "status": "blocked",
            "executed": False,
            "error": "requested_effect_pending",
            "error_code": "requested_effect_pending",
            "message": message,
        },
    )
    orchestrator._emit("task_blocked", {"reason": "requested_effect_pending", "objective": objective})
    publish_outcome(orchestrator)
    return message


def mark_unfinished_obligation(orchestrator: Any, objective: str) -> str:
    """Block on an unresolved non-effect obligation without inventing evidence."""

    state = orchestrator.agent_state
    if state.terminal_disposition not in (None, CompletionDisposition.COMPLETE.value):
        return _terminal_message(state)
    pending = tuple(getattr(state, "pending_obligations", lambda: ())())
    description = pending[0].description if pending else "requisito do objetivo"
    message = f"A tarefa nao foi concluida: permanece pendente {description}"
    _set_terminal(state, CompletionDisposition.BLOCK.value)
    state.project_last_result(
        "planner",
        {},
        {
            "ok": False,
            "done": True,
            "status": "blocked",
            "executed": False,
            "error": "task_obligation_pending",
            "error_code": "task_obligation_pending",
            "message": message,
        },
    )
    orchestrator._emit("task_blocked", {"reason": "task_obligation_pending", "objective": objective})
    publish_outcome(orchestrator)
    return message


def mark_reasoning_boundary_blocked(orchestrator: Any, objective: str) -> str:
    state = orchestrator.agent_state
    if state.terminal_disposition is not None:
        return _terminal_message(state)
    message = "A tarefa não pôde prosseguir após a fronteira de raciocínio."
    _set_terminal(state, CompletionDisposition.BLOCK.value)
    state.project_last_result(
        "planner",
        {},
        {
            "ok": False,
            "done": True,
            "status": "blocked",
            "executed": False,
            "error": "reasoning_boundary_blocked",
            "error_code": "reasoning_boundary_blocked",
            "message": message,
        },
    )
    orchestrator._emit("task_blocked", {"reason": "reasoning_boundary_blocked", "objective": objective})
    publish_outcome(orchestrator)
    return message


def mark_terminal_failure(orchestrator: Any) -> None:
    state = orchestrator.agent_state
    existing = getattr(state, "terminal_disposition", None)
    if existing == OperationalStatus.SUCCEEDED.value:
        existing = None
        _set_terminal(state, None)
    if existing not in (None, CompletionDisposition.COMPLETE.value):
        return
    result = getattr(state, "last_result", None)
    status = (
        operational_status_for(result.get("status"))
        if isinstance(result, Mapping)
        else None
    )
    if status == OperationalStatus.BLOCKED.value:
        _set_terminal(state, CompletionDisposition.BLOCK.value)
    elif status == OperationalStatus.PERMISSION_DENIED.value:
        _set_terminal(state, OperationalStatus.PERMISSION_DENIED.value)
    elif status == OperationalStatus.FAILED.value:
        _set_terminal(state, CompletionDisposition.FAIL.value)
    elif status in _NON_SUCCESS_STATUSES:
        _set_terminal(state, status)
    else:
        _set_terminal(state, CompletionDisposition.FAIL.value)


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
    if getattr(state, "terminal_disposition", None) == OperationalStatus.SUCCEEDED.value:
        _set_terminal(state, None)
    if getattr(state, "terminal_disposition", None) in (None, CompletionDisposition.COMPLETE.value):
        _set_terminal(
            state,
            status if status in _NON_SUCCESS_STATUSES else CompletionDisposition.BLOCK.value,
        )
        project = getattr(state, "project_last_result", None)
        if callable(project):
            project(
                "planner",
                {},
                {
                    "ok": False,
                    "done": True,
                    # Completion dispositions use lifecycle values such as
                    # ``block``; the canonical ToolResult boundary uses the
                    # public operational value ``blocked``.
                    "status": operational_status_for(state.terminal_disposition) or "blocked",
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
    _set_terminal(state, "cancelled")
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
    if isinstance(result, Mapping):
        # Preserve the established machine-readable deferred-condition
        # boundary for callers that use the completion return value as a
        # reason code.  The canonical ToolResult still carries the human
        # message and structured ToolError internally.
        if result.get("error_code") == "deferred_condition_blocked":
            return "deferred_condition_blocked"
        error = result.get("error")
        message = result.get("message")
        if result.get("error_code") == error and isinstance(message, str) and message.strip():
            return message.strip()
        message = error or message
        if isinstance(message, str) and message.strip():
            return message.strip()
    if disposition == CompletionDisposition.BLOCK.value:
        return "A tarefa foi bloqueada antes de concluir todos os efeitos."
    if disposition == CompletionDisposition.FAIL.value:
        return "A tarefa não pôde ser concluída."
    if disposition == OperationalStatus.CANCELLED.value:
        return "Tarefa cancelada pelo usuario."
    if disposition in _NON_SUCCESS_STATUSES:
        return f"A tarefa terminou com status operacional: {disposition}."
    return ""


__all__ = [
    "_set_terminal",
    "_terminal_message",
    "mark_reasoning_boundary_blocked",
    "mark_terminal_blocked",
    "mark_terminal_cancelled",
    "mark_terminal_failure",
    "mark_unfinished_effect",
    "mark_unfinished_obligation",
]

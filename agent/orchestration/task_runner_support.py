"""Small terminal-boundary helpers for the task runner."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agent.final_response import has_usable_partial_evidence
from agent.planning.task_completion import mark_terminal_blocked
from agent.runtime.operational_outcome import project_operational_outcome


def terminal_answer(
    orchestrator: Any,
    objective: str,
    on_chunk: Callable[[str], None] | None,
    fallback: str,
) -> str:
    """Retain safe read evidence while preserving a terminal non-success."""

    outcome = project_operational_outcome(
        orchestrator.agent_state,
        task_failed=bool(getattr(orchestrator, "_task_failed", False)),
        cancelled=bool(getattr(orchestrator, "_cancelled", False)),
    )
    history = getattr(orchestrator.agent_state, "tool_history", ())
    responder = getattr(orchestrator, "final_responder", None)
    if not has_usable_partial_evidence(outcome, history) or responder is None:
        return str(fallback)
    return str(
        responder.build_final_answer(
            objective,
            on_chunk=on_chunk,
            operational_outcome=outcome,
        )
    )


def checkpoint_error_answer(orchestrator: Any, error: Any) -> str:
    orchestrator._preserve_checkpoint = True
    return mark_terminal_blocked(
        orchestrator,
        reason_code=str(getattr(error, "reason_code", "CHECKPOINT_INVALID")),
        message=(
            "O checkpoint existente não pôde ser retomado com segurança "
            "e foi preservado para diagnóstico."
        ),
    )


__all__ = ["checkpoint_error_answer", "terminal_answer"]

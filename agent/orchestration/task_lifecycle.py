"""Lifecycle boundaries shared by the TaskRunner coordinator."""

from __future__ import annotations

from typing import Any

from agent.application_shutdown import require_application_invocations_drained
from agent.planning.completion_observations import publish_outcome
from agent.planning.task_completion import (
    allow_linear_completion,
    mark_terminal_blocked,
    mark_terminal_cancelled,
    mark_terminal_failure,
    review_task_completion,
)
from agent.runtime.logging import logger


class TaskLifecycleMixin:
    orchestrator: Any

    def _reset_missing_input_state(self) -> None:
        """Discard stale task evidence before recording a missing-input result."""

        reset_task = getattr(self.orchestrator, "_reset_task_state", None)
        if callable(reset_task):
            reset_task("")
            return
        state = self.orchestrator.agent_state
        reset_execution = getattr(state, "reset_execution", None)
        if callable(reset_execution):
            reset_execution()
        reset_progression = getattr(state, "reset_task_progression", None)
        if callable(reset_progression):
            reset_progression(())
        state.objective = None
        state.last_result = None
        state.last_tool = None
        state.last_args = None
        state.tool_history = []
        clear_terminal = getattr(state, "clear_terminal_disposition", None)
        if callable(clear_terminal):
            clear_terminal()
        else:
            state.terminal_disposition = None

    def _resume_terminal_checkpoint(self, objective: str) -> str:
        """Report a terminal checkpoint without reopening a fresh route."""

        disposition = getattr(self.orchestrator.agent_state, "terminal_disposition", None)
        if disposition == "complete":
            review = review_task_completion(self.orchestrator)
            result = getattr(self.orchestrator.agent_state, "last_result", None)
            result_is_success = (
                isinstance(result, dict)
                and result.get("status") == "succeeded"
                and result.get("ok") is True
            )
            if not review.accepted or not result_is_success:
                self.orchestrator._preserve_checkpoint = True
                return mark_terminal_blocked(
                    self.orchestrator,
                    reason_code="CHECKPOINT_TERMINAL_EVIDENCE_MISSING",
                    message=(
                        "O checkpoint terminal não contém evidência suficiente "
                        "para iniciar uma nova tarefa com sucesso."
                    ),
                )
        if disposition == "cancelled":
            self.orchestrator._cancelled = True
            self.orchestrator._preserve_checkpoint = True
        blocker = allow_linear_completion(self.orchestrator, objective)
        if blocker is not None:
            return str(blocker)
        result = getattr(self.orchestrator.agent_state, "last_result", None)
        if isinstance(result, dict):
            message = result.get("message") or result.get("answer")
            if isinstance(message, str) and message.strip():
                return message.strip()
        return "A tarefa ja possui um resultado terminal no checkpoint."

    def _handle_interrupt(self) -> str:
        self.orchestrator.cancellation_token.cancel()
        self._require_invocation_lifetime_closed()
        message = mark_terminal_cancelled(self.orchestrator)
        self.orchestrator._save_checkpoint()
        return f"{message} O progresso foi salvo e pode ser retomado posteriormente."

    def _require_invocation_lifetime_closed(self) -> None:
        gateway = getattr(self.orchestrator, "tool_invocation_gateway", None)
        if gateway is not None:
            require_application_invocations_drained(gateway)

    def _cleanup(self, original_count: int) -> None:
        orchestrator = self.orchestrator
        try:
            self._require_invocation_lifetime_closed()
            if orchestrator._task_failed:
                orchestrator.workspace.rollback()
            while len(orchestrator.session.messages) > original_count:
                orchestrator.session.messages.pop()
            maximum = orchestrator.agent_state.max_history_turns
            orchestrator.agent_state.conversation_history = (
                orchestrator.agent_state.conversation_history[-maximum:]
            )
            orchestrator.context_manager.maybe_compress_context()
            try:
                orchestrator._persist_memory_to_file()
            except Exception:
                orchestrator._task_failed = True
                state = orchestrator.agent_state
                result = {
                    "ok": False,
                    "done": True,
                    "status": "failed",
                    "executed": False,
                    "error": "TASK_CLEANUP_FAILURE",
                    "error_code": "TASK_CLEANUP_FAILURE",
                    "message": "Falha ao persistir a memoria ao finalizar a tarefa.",
                }
                project = getattr(state, "project_last_result", None)
                if callable(project):
                    project("orchestrator", {}, result)
                else:
                    state.last_result = result
                mark_terminal_failure(orchestrator)
                publish_outcome(orchestrator)
                logger.exception("Falha ao persistir memória ao finalizar a tarefa.")
                raise
            if not orchestrator._cancelled and not getattr(
                orchestrator, "_preserve_checkpoint", False
            ):
                orchestrator._delete_checkpoint()
        except Exception:
            raise


__all__ = ["TaskLifecycleMixin"]

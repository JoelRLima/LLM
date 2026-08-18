"""Lifecycle boundaries shared by the TaskRunner coordinator."""

from __future__ import annotations

from typing import Any

from agent.planning.completion_observations import publish_outcome
from agent.planning.task_completion import allow_linear_completion, mark_terminal_cancelled
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
        state.terminal_disposition = None

    def _resume_terminal_checkpoint(self, objective: str) -> str:
        """Report a terminal checkpoint without reopening a fresh route."""

        disposition = getattr(self.orchestrator.agent_state, "terminal_disposition", None)
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
        message = mark_terminal_cancelled(self.orchestrator)
        self.orchestrator._save_checkpoint()
        return f"{message} O progresso foi salvo e pode ser retomado posteriormente."

    def _cleanup(self, original_count: int) -> None:
        orchestrator = self.orchestrator
        try:
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
                state.terminal_disposition = "fail"
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

"""Lifecycle boundaries shared by the TaskRunner coordinator."""

from __future__ import annotations

from collections.abc import Mapping
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
from agent.runtime.mutation_evidence import project_mutation_evidence
from agent.runtime.outcome_taxonomy import NON_SUCCESS_STATUSES


def _has_observed_task_mutation(state: Any) -> bool:
    for entry in getattr(state, "tool_history", ()) or ():
        if not isinstance(entry, Mapping):
            continue
        evidence = project_mutation_evidence(entry.get("result"))
        if evidence.occurred or evidence.survives:
            return True
    return False


def _has_rollback_material(workspace: Any) -> bool:
    """Return whether cleanup has task-owned state worth restoring."""

    checker = getattr(workspace, "has_pending_mutations", None)
    if callable(checker):
        try:
            return bool(checker())
        except Exception:
            # A broken inspection must not suppress the conservative cleanup
            # attempt; the rollback result remains authoritative below.
            return True

    known_container = False
    for name in ("restore_points", "created_files", "_task_transactions"):
        if hasattr(workspace, name):
            known_container = True
            if bool(getattr(workspace, name)):
                return True
    # Compatibility workspaces without an introspection surface retain the
    # historical cleanup attempt, while public rollback truth is still based
    # on observed mutation evidence.
    return not known_container


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
                isinstance(result, Mapping)
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
        if isinstance(result, Mapping):
            message = result.get("message") or result.get("answer")
            if isinstance(message, str) and message.strip():
                return message.strip()
        return "A tarefa ja possui um resultado terminal no checkpoint."

    def _handle_interrupt(self) -> str:
        self.orchestrator.cancellation_token.cancel()
        self._require_invocation_lifetime_closed()
        message = mark_terminal_cancelled(self.orchestrator)
        saved = self.orchestrator._save_checkpoint()
        if saved:
            return f"{message} O progresso foi salvo e pode ser retomado posteriormente."
        return f"{message} Não foi possível confirmar o salvamento do progresso; a retomada não está garantida."

    def _require_invocation_lifetime_closed(self) -> None:
        gateway = getattr(self.orchestrator, "tool_invocation_gateway", None)
        if gateway is not None:
            require_application_invocations_drained(gateway)

    def _cleanup(self, original_count: int) -> None:
        orchestrator = self.orchestrator
        try:
            self._require_invocation_lifetime_closed()
            disposition = getattr(orchestrator.agent_state, "terminal_disposition", None)
            # ``unverified`` is the explicit assisted/approved escape hatch
            # for a mutation whose validation is unavailable.  It remains a
            # non-success public outcome, but preserving that mutation is
            # allowed by policy.  Every actual failure/block/cancellation,
            # including an unverified result that also carries the task
            # failure flag, must still use the task rollback authority.
            rollback_statuses = (NON_SUCCESS_STATUSES - {"unverified"}) | {
                "fail",
                "block",
            }
            should_rollback = bool(orchestrator._task_failed) or disposition in rollback_statuses
            workspace = orchestrator.workspace
            if should_rollback and _has_rollback_material(workspace):
                observed_mutation = _has_observed_task_mutation(orchestrator.agent_state)
                rollback_result = workspace.rollback()
                # Real WorkspaceManager instances return a strict bool.  A
                # narrow compatibility seam may still return None; retain
                # its historical success convention without allowing any
                # other falsey value to masquerade as restoration.
                rollback_ok = rollback_result is not False
                state = orchestrator.agent_state
                state._task_rollback_occurred = observed_mutation
                state._task_rollback_succeeded = bool(rollback_ok) if observed_mutation else None
                if not rollback_ok:
                    orchestrator._task_failed = True
                    result = {
                        "ok": False,
                        "done": True,
                        "status": "failed",
                        "executed": False,
                        "error": "TASK_CLEANUP_FAILURE",
                        "error_code": "TASK_CLEANUP_FAILURE",
                        "message": "Falha ao restaurar todas as mutações da tarefa.",
                    }
                    project = getattr(state, "project_last_result", None)
                    if callable(project):
                        project("orchestrator", {}, result)
                    else:
                        state.last_result = result
                    mark_terminal_failure(orchestrator)
                    publish_outcome(orchestrator)
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

"""Lifecycle boundaries shared by the TaskRunner coordinator."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent.application_shutdown import require_application_invocations_drained
from agent.planning.completion_observations import publish_outcome
from agent.planning.task_completion import (
    allow_linear_completion,
    mark_terminal_blocked,
    mark_terminal_failure,
    review_task_completion,
)
from agent.runtime.logging import logger
from agent.runtime.mutation_evidence import project_mutation_evidence
from agent.runtime.outcome_taxonomy import NON_SUCCESS_STATUSES
from agent.tools.result_completeness import canonical_result_successful


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

        self.orchestrator._reset_task_state("")

    def _resume_terminal_checkpoint(self, objective: str) -> str:
        """Report a terminal checkpoint without reopening a fresh route."""

        disposition = getattr(self.orchestrator.agent_state, "terminal_disposition", None)
        if disposition == "complete":
            review = review_task_completion(self.orchestrator)
            result = getattr(self.orchestrator.agent_state, "last_result", None)
            result_is_success = (
                isinstance(result, Mapping)
                and canonical_result_successful(result)
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
        self.orchestrator._preserve_checkpoint = True
        self.orchestrator.cancellation_token.cancel()
        self._require_invocation_lifetime_closed()
        policy = getattr(self.orchestrator, "task_policy", None)
        if policy is not None:
            policy.pause_active_segment()
        state = getattr(self.orchestrator, "agent_state", None)
        record_interruption = getattr(state, "record_continuity_interruption", None)
        if callable(record_interruption):
            record_interruption()
        saved = self.orchestrator._save_checkpoint()
        if saved:
            return "Tarefa pausada por interrupcao. O progresso foi salvo e pode ser retomado posteriormente."
        return "Tarefa pausada por interrupcao. Nao foi possivel confirmar o salvamento do progresso; a retomada nao esta garantida."

    def _require_invocation_lifetime_closed(self) -> None:
        gateway = getattr(self.orchestrator, "tool_invocation_gateway", None)
        if gateway is not None:
            require_application_invocations_drained(gateway)

    def _cleanup(self, original_count: int) -> None:
        orchestrator = self.orchestrator
        try:
            policy = getattr(orchestrator, "task_policy", None)
            if policy is not None:
                policy.pause_active_segment()
            self._require_invocation_lifetime_closed()
            disposition = getattr(orchestrator.agent_state, "terminal_disposition", None)
            interrupted_hard_failure, should_rollback = _rollback_decision(
                orchestrator, disposition
            )
            if should_rollback:
                _rollback_if_needed(orchestrator)
            _finalize_interrupted_hard_failure(orchestrator, interrupted_hard_failure)
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


def _continuity_interrupted(state: Any) -> bool:
    continuity = getattr(state, "continuity", None)
    return (
        isinstance(continuity, Mapping)
        and continuity.get("interrupted") is True
        and getattr(state, "terminal_disposition", None) is None
    )


def _rollback_decision(orchestrator: Any, disposition: Any) -> tuple[bool, bool]:
    """Keep pause-only cleanup separate from canonical failure authority."""

    interrupted = _continuity_interrupted(orchestrator.agent_state)
    rollback_statuses = (NON_SUCCESS_STATUSES - {"unverified"}) | {"fail", "block"}
    task_failed = bool(orchestrator._task_failed)
    return task_failed and interrupted, task_failed or (not interrupted and disposition in rollback_statuses)


def _rollback_if_needed(orchestrator: Any) -> None:
    workspace = orchestrator.workspace
    if not _has_rollback_material(workspace):
        return
    observed_mutation = _has_observed_task_mutation(orchestrator.agent_state)
    rollback_result = workspace.rollback()
    # Real WorkspaceManager instances return a strict bool.  A narrow
    # compatibility seam may still return None; retain its historical success
    # convention without allowing another falsey value to masquerade as restoration.
    rollback_ok = rollback_result is not False
    state = orchestrator.agent_state
    state._task_rollback_occurred = observed_mutation
    state._task_rollback_succeeded = bool(rollback_ok) if observed_mutation else None
    if rollback_ok:
        return
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


def _finalize_interrupted_hard_failure(orchestrator: Any, interrupted: bool) -> None:
    if not interrupted:
        return
    # A hard failure that coincides with interruption is not a resumable pause.
    # Reuse the canonical failure marker and remove the pause snapshot.
    if getattr(orchestrator.agent_state, "terminal_disposition", None) is None:
        mark_terminal_failure(orchestrator)
        publish_outcome(orchestrator)
    orchestrator._preserve_checkpoint = False

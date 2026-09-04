"""Explicit resume input resolution for the canonical task runner."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Optional

from agent.checkpoint_manager import CheckpointLoadError
from agent.continuity import (
    REASON_CHECKPOINT_ABSENT,
    REASON_CHECKPOINT_RESUMABLE,
    REASON_CHECKPOINT_ROOT_MISSING,
    REASON_HIERARCHICAL_RESUME_UNSUPPORTED,
    REASON_TASK_DEFINITION_BINDING_MISSING,
    TaskContinuityStatus,
    classify_checkpoint_document,
)
from agent.orchestration.task_runner_resume_commit import (
    REASON_TASK_RESUME_COMMIT_STATE_UNCERTAIN,
    ExplicitResumeRefused,
    commit_explicit_resume,
    reconcile_interrupted_resume_commit,
)
from agent.planning.task_completion import mark_terminal_blocked
from agent.runtime.logging import logger
from agent.runtime.task_directives import (
    DeliberationProfile,
    TaskDirective,
    TaskRunDirective,
)

REASON_TASK_RESUME_INTERRUPTED_BEFORE_COMMIT = "TASK_RESUME_INTERRUPTED_BEFORE_COMMIT"


@dataclass
class TaskInputs:
    objective: str
    resumed: bool
    original_message_count: int
    resume_reason: str | None = None
    task_run_directive: TaskRunDirective | None = None

    def __post_init__(self) -> None:
        if self.task_run_directive is None:
            self.task_run_directive = TaskRunDirective(
                TaskDirective.AUTO,
                DeliberationProfile.NORMAL,
                self.objective,
            )
        elif not isinstance(self.task_run_directive, TaskRunDirective):
            raise TypeError("task_run_directive must be a TaskRunDirective")


def resume_refusal_message(reason_code: str) -> str:
    return (
        "A tarefa nao pode ser retomada com seguranca "
        f"({reason_code}); o checkpoint foi preservado."
    )


def handle_explicit_resume_interrupt(runner: Any, explicit_resume: bool) -> str | None:
    if not explicit_resume or runner._resume_attempt_committed:
        return None
    runner._resume_commit_failed = True
    runner.orchestrator._preserve_checkpoint = True
    runner.orchestrator._resume_refusal_reason = REASON_TASK_RESUME_INTERRUPTED_BEFORE_COMMIT
    runner.orchestrator._last_failure_code = REASON_TASK_RESUME_INTERRUPTED_BEFORE_COMMIT
    return resume_refusal_message(REASON_TASK_RESUME_INTERRUPTED_BEFORE_COMMIT)


def resolve_inputs(
    runner: Any,
    objective: Optional[str],
    original_count: int,
    *,
    explicit_resume: bool = False,
    task_run_directive: TaskRunDirective | None = None,
) -> TaskInputs | None:
    """Resolve a fresh objective or restore one supported checkpoint."""

    orchestrator = runner.orchestrator
    if explicit_resume and task_run_directive is not None:
        raise ExplicitResumeRefused("TASK_RESUME_DIRECTIVE_OVERRIDE_NOT_ALLOWED")
    if explicit_resume and objective:
        raise ExplicitResumeRefused("TASK_RESUME_OBJECTIVE_NOT_ALLOWED")
    if objective:
        selected = task_run_directive or TaskRunDirective(
            TaskDirective.AUTO,
            DeliberationProfile.NORMAL,
            objective,
        )
        if selected.subject != objective:
            raise ValueError("TASK_DIRECTIVE_SUBJECT_MISMATCH")
        return TaskInputs(
            selected.canonical_objective(),
            False,
            original_count,
            task_run_directive=selected,
        )
    if task_run_directive is not None:
        raise ValueError("TASK_DIRECTIVE_OBJECTIVE_REQUIRED")
    checkpoint = _load_checkpoint(orchestrator, explicit_resume=explicit_resume)
    if checkpoint is None:
        if explicit_resume:
            raise ExplicitResumeRefused(REASON_CHECKPOINT_ABSENT)
        return None
    if explicit_resume:
        _require_explicit_resume_checkpoint(orchestrator, checkpoint)
    return _restore_checkpoint(runner, checkpoint, original_count, explicit_resume=explicit_resume)


def _load_checkpoint(orchestrator: Any, *, explicit_resume: bool) -> Any:
    try:
        checkpoint = orchestrator._load_checkpoint()
    except CheckpointLoadError as exc:
        if explicit_resume:
            raise ExplicitResumeRefused(
                str(getattr(exc, "reason_code", "CHECKPOINT_INVALID"))
            ) from exc
        raise
    return checkpoint


def _restore_checkpoint(
    runner: Any,
    checkpoint: Any,
    original_count: int,
    *,
    explicit_resume: bool,
) -> TaskInputs | None:
    orchestrator = runner.orchestrator
    try:
        orchestrator.agent_state.from_checkpoint_dict(
            checkpoint,
            retry_failed=bool(orchestrator.session.config.get("resume_retry_failed", False)),
            retry_skipped=bool(orchestrator.session.config.get("resume_retry_skipped", False)),
            effect_authority=orchestrator,
            admission_authority=getattr(orchestrator, "admission_authority", None),
        )
        refresh_policy = getattr(orchestrator, "_refresh_task_policy", None)
        if callable(refresh_policy):
            refresh_policy()
    except ValueError:
        if explicit_resume:
            raise ExplicitResumeRefused("CHECKPOINT_INVALID") from None
        orchestrator._preserve_checkpoint = True
        orchestrator.agent_state.root_task_id = None
        restored_objective = str(checkpoint.get("objective") or "checkpoint invalido")
        mark_terminal_blocked(
            orchestrator,
            reason_code="CHECKPOINT_INVALID_TERMINAL_DISPOSITION",
            message="O checkpoint contem um estado terminal incompativel e foi preservado.",
        )
        return TaskInputs(
            restored_objective,
            True,
            original_count,
            task_run_directive=TaskRunDirective(
                TaskDirective.AUTO,
                DeliberationProfile.NORMAL,
                restored_objective,
            ),
        )
    lifecycle = getattr(orchestrator.agent_state, "hierarchical_lifecycle", {})
    if isinstance(lifecycle, Mapping) and lifecycle.get("status") == "running":
        if explicit_resume:
            raise ExplicitResumeRefused(REASON_HIERARCHICAL_RESUME_UNSUPPORTED)
        orchestrator._preserve_checkpoint = True
        mark_terminal_blocked(
            orchestrator,
            reason_code=REASON_HIERARCHICAL_RESUME_UNSUPPORTED,
            message=(
                "A execucao hierarquica interrompida nao pode ser retomada "
                "com seguranca; o checkpoint foi preservado para auditoria."
            ),
            status="block",
        )
        resumed_objective = str(orchestrator.agent_state.objective or "checkpoint")
        return TaskInputs(
            resumed_objective,
            True,
            original_count,
            task_run_directive=getattr(orchestrator.agent_state, "task_run_directive", None),
        )
    restored = orchestrator.agent_state.objective
    if not restored:
        orchestrator._delete_checkpoint()
        return None
    print(chr(10) + 'Checkpoint encontrado. Retomando tarefa: "' + str(restored) + '"')
    logger.info("Retomando tarefa a partir de checkpoint: %s", restored)
    raw_continuity = checkpoint.get("continuity")
    resume_reason = REASON_CHECKPOINT_RESUMABLE
    if isinstance(raw_continuity, Mapping):
        raw_reason = raw_continuity.get("interruption_reason")
        if raw_reason == "keyboard_interrupt":
            resume_reason = "keyboard_interrupt"
        elif raw_continuity.get("interrupted") is True:
            resume_reason = "task_paused"
    task_run_directive = getattr(orchestrator.agent_state, "task_run_directive", None)
    if not isinstance(task_run_directive, TaskRunDirective):
        raise ValueError("Checkpoint task_run_directive is missing after restore.")
    return TaskInputs(
        str(restored),
        True,
        original_count,
        resume_reason,
        task_run_directive=task_run_directive,
    )


def _require_explicit_resume_checkpoint(orchestrator: Any, checkpoint: Any) -> None:
    snapshot = classify_checkpoint_document(
        checkpoint,
        workspace_id=str(
            getattr(getattr(orchestrator, "workspace_paths", None), "workspace_id", "workspace")
        ),
    )
    if snapshot.status not in {TaskContinuityStatus.RESUMABLE, TaskContinuityStatus.PAUSED}:
        raise ExplicitResumeRefused(snapshot.reason_code)
    if not snapshot.root_task_id:
        raise ExplicitResumeRefused(REASON_CHECKPOINT_ROOT_MISSING)
    if snapshot.task_definition_ref is None:
        raise ExplicitResumeRefused(REASON_TASK_DEFINITION_BINDING_MISSING)


def emit_resume_event(runner: Any, inputs: TaskInputs) -> None:
    """Publish the bounded semantic fact after restore and observation attach."""

    emit = getattr(runner.orchestrator, "_emit", None)
    if not callable(emit):
        return
    continuity = getattr(runner.orchestrator.agent_state, "continuity", None)
    data: dict[str, Any] = {
        "checkpoint_reason": inputs.resume_reason or REASON_CHECKPOINT_RESUMABLE,
    }
    if isinstance(continuity, Mapping):
        previous = continuity.get("resumed_from_run_id")
        generation = continuity.get("resume_generation")
        if isinstance(previous, str) and previous.strip():
            data["resumed_from_run_id"] = previous
        if isinstance(generation, int) and not isinstance(generation, bool):
            data["resume_generation"] = generation
    try:
        emit("task_resumed", data)
    except Exception as exc:
        logger.warning("Resume event emission failed: %s", type(exc).__name__)


__all__ = [
    "commit_explicit_resume",
    "ExplicitResumeRefused",
    "REASON_TASK_RESUME_COMMIT_STATE_UNCERTAIN",
    "REASON_TASK_RESUME_INTERRUPTED_BEFORE_COMMIT",
    "reconcile_interrupted_resume_commit",
    "TaskInputs",
    "emit_resume_event",
    "handle_explicit_resume_interrupt",
    "resolve_inputs",
    "resume_refusal_message",
]

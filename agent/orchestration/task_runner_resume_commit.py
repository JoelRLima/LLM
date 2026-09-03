"""Persistence-boundary handling for explicit task resume."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from agent.runtime.logging import logger
from agent.state_checkpoint import validate_continuity_metadata

REASON_TASK_RESUME_COMMIT_STATE_UNCERTAIN = "TASK_RESUME_COMMIT_STATE_UNCERTAIN"

_RESUME_COMMIT_OLD = "OLD"
_RESUME_COMMIT_COMMITTED = "COMMITTED"
_RESUME_COMMIT_UNCERTAIN = "UNCERTAIN"


@dataclass(frozen=True, slots=True)
class _ResumeCommitExpectation:
    """Immutable lineage facts captured around the explicit-resume commit."""

    root_task_id: str
    previous_run_id: str | None
    previous_generation: int | None
    previous_continuity_present: bool
    new_run_id: str | None = None
    new_generation: int | None = None
    expected_resumed_from_run_id: str | None = None

    def is_bound(self) -> bool:
        return (
            isinstance(self.new_run_id, str)
            and bool(self.new_run_id.strip())
            and isinstance(self.new_generation, int)
            and not isinstance(self.new_generation, bool)
            and self.new_generation >= 0
        )


class ExplicitResumeRefused(RuntimeError):
    """A requested resume was rejected before restore or task execution."""

    code = "TASK_RESUME_UNAVAILABLE"

    def __init__(self, reason_code: str) -> None:
        self.reason_code = str(reason_code or self.code)
        super().__init__(self.reason_code)


def _non_empty_text(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value


def _capture_resume_commit_expectation(runner: Any) -> _ResumeCommitExpectation | None:
    """Capture the durable predecessor before a resume correlation mutates it."""

    try:
        state = runner.orchestrator.agent_state
        root_task_id = _non_empty_text(getattr(state, "root_task_id", None))
        if root_task_id is None:
            return None
        raw_continuity = getattr(state, "continuity", None)
        if raw_continuity is None:
            return _ResumeCommitExpectation(
                root_task_id=root_task_id,
                previous_run_id=None,
                previous_generation=None,
                previous_continuity_present=False,
            )
        if not isinstance(raw_continuity, Mapping):
            return None
        continuity = validate_continuity_metadata(raw_continuity)
        previous_generation = continuity.get("resume_generation")
        if (
            isinstance(previous_generation, bool)
            or not isinstance(previous_generation, int)
            or previous_generation < 0
        ):
            return None
        previous_run_id = continuity.get("last_run_id")
        if previous_run_id is not None and _non_empty_text(previous_run_id) is None:
            return None
        return _ResumeCommitExpectation(
            root_task_id=root_task_id,
            previous_run_id=previous_run_id,
            previous_generation=previous_generation,
            previous_continuity_present=True,
        )
    except (AttributeError, TypeError, ValueError):
        return None


def _bind_resume_commit_expectation(
    runner: Any,
    expectation: _ResumeCommitExpectation | None,
) -> _ResumeCommitExpectation | None:
    """Bind the expected new lineage from the canonical post-correlation state."""

    if expectation is None:
        return None
    try:
        orchestrator = runner.orchestrator
        state = orchestrator.agent_state
        correlation = getattr(orchestrator, "run_correlation", None)
        run_id = _non_empty_text(getattr(correlation, "run_id", None))
        correlation_root = _non_empty_text(getattr(correlation, "root_task_id", None))
        state_root = _non_empty_text(getattr(state, "root_task_id", None))
        if (
            run_id is None
            or correlation_root != expectation.root_task_id
            or state_root != expectation.root_task_id
        ):
            return None
        continuity = validate_continuity_metadata(getattr(state, "continuity", None))
        new_generation = continuity.get("resume_generation")
        if (
            isinstance(new_generation, bool)
            or not isinstance(new_generation, int)
            or new_generation < 0
            or continuity.get("last_run_id") != run_id
            or continuity.get("interrupted") is not False
        ):
            return None
        if expectation.previous_generation is None:
            if new_generation != 0:
                return None
        elif new_generation != expectation.previous_generation + 1:
            return None
        resumed_from_run_id = continuity.get("resumed_from_run_id")
        if resumed_from_run_id != expectation.previous_run_id:
            return None
        if run_id == expectation.previous_run_id:
            return None
        return replace(
            expectation,
            new_run_id=run_id,
            new_generation=new_generation,
            expected_resumed_from_run_id=resumed_from_run_id,
        )
    except (AttributeError, TypeError, ValueError):
        return None


def _validated_persisted_continuity(document: Mapping[str, Any]) -> dict[str, Any] | None:
    raw_continuity = document.get("continuity")
    if not isinstance(raw_continuity, Mapping):
        return None
    try:
        return validate_continuity_metadata(raw_continuity)
    except (TypeError, ValueError):
        return None


def _same_root(document: Mapping[str, Any], expectation: _ResumeCommitExpectation) -> bool:
    persisted_root = _non_empty_text(document.get("root_task_id"))
    return persisted_root == expectation.root_task_id


def _matches_committed_lineage(
    document: Mapping[str, Any], expectation: _ResumeCommitExpectation
) -> bool:
    if not expectation.is_bound() or not _same_root(document, expectation):
        return False
    continuity = _validated_persisted_continuity(document)
    if continuity is None:
        return False
    return (
        continuity.get("last_run_id") == expectation.new_run_id
        and continuity.get("resume_generation") == expectation.new_generation
        and continuity.get("resumed_from_run_id")
        == expectation.expected_resumed_from_run_id
        and continuity.get("interrupted") is False
    )


def _matches_old_lineage(
    document: Mapping[str, Any], expectation: _ResumeCommitExpectation
) -> bool:
    if not _same_root(document, expectation):
        return False
    if not expectation.previous_continuity_present:
        return document.get("continuity") is None
    continuity = _validated_persisted_continuity(document)
    return (
        continuity is not None
        and continuity.get("last_run_id") == expectation.previous_run_id
        and continuity.get("resume_generation") == expectation.previous_generation
    )


def _canonical_checkpoint_loader(orchestrator: Any) -> Any:
    manager = getattr(orchestrator, "checkpoint_manager", None)
    load = getattr(manager, "load", None)
    if callable(load):
        return load
    # Compatibility owners route this method to CheckpointManager.load().
    load = getattr(orchestrator, "_load_checkpoint", None)
    return load if callable(load) else None


def reconcile_interrupted_resume_commit(runner: Any) -> str:
    """Classify the canonical checkpoint after an interrupted resume save."""

    expectation = getattr(runner, "_resume_commit_expectation", None)
    if not isinstance(expectation, _ResumeCommitExpectation) or not expectation.is_bound():
        return _RESUME_COMMIT_UNCERTAIN
    load = _canonical_checkpoint_loader(runner.orchestrator)
    if not callable(load):
        return _RESUME_COMMIT_UNCERTAIN
    try:
        persisted = load()
    except BaseException as exc:
        logger.warning(
            "Interrupted explicit resume reconciliation could not load checkpoint: %s",
            type(exc).__name__,
        )
        return _RESUME_COMMIT_UNCERTAIN
    if not isinstance(persisted, Mapping):
        return _RESUME_COMMIT_UNCERTAIN
    if _matches_committed_lineage(persisted, expectation):
        return _RESUME_COMMIT_COMMITTED
    if _matches_old_lineage(persisted, expectation):
        return _RESUME_COMMIT_OLD
    return _RESUME_COMMIT_UNCERTAIN


def commit_explicit_resume(runner: Any) -> None:
    """Require the existing checkpoint owner to durably admit a new attempt."""

    try:
        confirmed = runner.orchestrator._save_checkpoint()
    except KeyboardInterrupt:
        disposition = reconcile_interrupted_resume_commit(runner)
        if disposition == _RESUME_COMMIT_COMMITTED:
            runner._resume_attempt_committed = True
            runner._resume_commit_failed = False
            raise
        runner._resume_attempt_committed = False
        runner._resume_commit_failed = True
        if disposition == _RESUME_COMMIT_UNCERTAIN:
            raise ExplicitResumeRefused(REASON_TASK_RESUME_COMMIT_STATE_UNCERTAIN) from None
        raise
    except Exception as exc:
        runner._resume_commit_failed = True
        logger.warning("Explicit resume checkpoint commit failed: %s", type(exc).__name__)
        raise ExplicitResumeRefused("TASK_RESUME_COMMIT_FAILED") from exc
    if confirmed is not True:
        runner._resume_commit_failed = True
        raise ExplicitResumeRefused("TASK_RESUME_COMMIT_FAILED")


def start_explicit_resume_attempt(runner: Any, start_correlation: Any) -> None:
    """Prepare, commit, and mark one explicit-resume attempt."""

    try:
        expectation = _capture_resume_commit_expectation(runner)
        runner._resume_commit_expectation = expectation
        if expectation is None:
            raise ExplicitResumeRefused(REASON_TASK_RESUME_COMMIT_STATE_UNCERTAIN)
        if not callable(start_correlation):
            raise RuntimeError("orchestrator does not expose the resume correlation owner")
        start_correlation(resumed=True)
        expectation = _bind_resume_commit_expectation(runner, expectation)
        runner._resume_commit_expectation = expectation
        if expectation is None:
            raise ExplicitResumeRefused(REASON_TASK_RESUME_COMMIT_STATE_UNCERTAIN)
        commit_explicit_resume(runner)
        runner._resume_attempt_committed = True
    except KeyboardInterrupt:
        if not runner._resume_attempt_committed:
            runner._resume_commit_failed = True
        raise
    except ExplicitResumeRefused:
        runner._resume_commit_failed = True
        raise
    except Exception as exc:
        runner._resume_commit_failed = True
        logger.warning(
            "Explicit resume correlation/commit failed: %s",
            type(exc).__name__,
        )
        raise ExplicitResumeRefused("TASK_RESUME_COMMIT_FAILED") from exc


__all__ = [
    "ExplicitResumeRefused",
    "REASON_TASK_RESUME_COMMIT_STATE_UNCERTAIN",
    "commit_explicit_resume",
    "reconcile_interrupted_resume_commit",
    "start_explicit_resume_attempt",
]

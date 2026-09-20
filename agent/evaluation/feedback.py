"""Explicit human feedback contracts and service."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable
from uuid import uuid4

from agent.application_result import AgentRunResult
from agent.evaluation.feedback_store import (
    FEEDBACK_ALREADY_EXISTS,
    FEEDBACK_NOT_FOUND,
    FEEDBACK_REVISION_CONFLICT,
    FeedbackStore,
)
from agent.evaluation.feedback_time import default_feedback_clock, resolve_feedback_time
from agent.evaluation.feedback_validators import (
    normalize_comment,
    validate_record_content,
    validate_record_identity,
)
from agent.observability.trace_types import TraceMetadata
from agent.runtime.paths import WorkspacePaths

FEEDBACK_SCHEMA_VERSION = 1
MAX_FEEDBACK_COMMENT_CHARS = 2_000
MAX_FEEDBACK_RECORDS = 4_096
MAX_FEEDBACK_DOCUMENT_BYTES = 8 * 1024 * 1024

FEEDBACK_TARGET_INVALID = "FEEDBACK_TARGET_INVALID"
FEEDBACK_COMMENT_TOO_LARGE = "FEEDBACK_COMMENT_TOO_LARGE"


class FeedbackVerdict(str, Enum):
    CORRECT = "correct"
    PARTIAL = "partial"
    INCORRECT = "incorrect"
    NOT_RATED = "not_rated"


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise FeedbackError(FEEDBACK_TARGET_INVALID, f"{label} is required")
    return value


class FeedbackError(RuntimeError):
    """Stable human-feedback contract error."""

    def __init__(self, reason_code: str, message: str) -> None:
        self.reason_code = reason_code
        super().__init__(message)


def _record_error(reason_code: str, message: str) -> Exception:
    return FeedbackError(reason_code, message)


@dataclass(frozen=True, slots=True)
class FeedbackTarget:
    run_id: str
    root_task_id: str
    terminal_status: str
    completed_at: str

    def __post_init__(self) -> None:
        _text(self.run_id, "run_id")
        _text(self.root_task_id, "root_task_id")
        status = _text(self.terminal_status, "terminal_status").casefold()
        if status in {"active", "running", "pending", "in_progress"}:
            raise FeedbackError(FEEDBACK_TARGET_INVALID, "feedback target is not terminal")
        _text(self.completed_at, "completed_at")

    @classmethod
    def from_run_result(cls, result: AgentRunResult) -> "FeedbackTarget":
        snapshot = result.snapshot
        if snapshot is None:
            raise FeedbackError(FEEDBACK_TARGET_INVALID, "run result has no canonical snapshot")
        return cls(
            run_id=snapshot.correlation.run_id,
            root_task_id=snapshot.correlation.root_task_id,
            terminal_status=snapshot.status,
            completed_at=snapshot.created_at,
        )

    @classmethod
    def from_trace_metadata(cls, metadata: TraceMetadata) -> "FeedbackTarget":
        if not isinstance(metadata, TraceMetadata) or metadata.active:
            raise FeedbackError(FEEDBACK_TARGET_INVALID, "trace is still active")
        if not metadata.end_time:
            raise FeedbackError(FEEDBACK_TARGET_INVALID, "trace has no completion timestamp")
        return cls(
            run_id=metadata.run_id,
            root_task_id=metadata.root_task_id,
            terminal_status=metadata.status,
            completed_at=metadata.end_time,
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "run_id": self.run_id,
            "root_task_id": self.root_task_id,
            "terminal_status": self.terminal_status,
            "completed_at": self.completed_at,
        }

    @classmethod
    def from_dict(cls, value: object) -> "FeedbackTarget":
        if not isinstance(value, dict):
            raise FeedbackError(FEEDBACK_TARGET_INVALID, "feedback target must be an object")
        try:
            return cls(
                run_id=value["run_id"],
                root_task_id=value["root_task_id"],
                terminal_status=value["terminal_status"],
                completed_at=value["completed_at"],
            )
        except FeedbackError:
            raise
        except Exception as exc:
            raise FeedbackError(FEEDBACK_TARGET_INVALID, "feedback target is invalid") from exc


@dataclass(frozen=True, slots=True)
class FeedbackRecord:
    schema_version: int
    feedback_id: str
    revision: int
    target: FeedbackTarget
    verdict: FeedbackVerdict
    comment: str | None
    comment_redacted: bool
    created_at: str
    supersedes_revision: int | None

    def __post_init__(self) -> None:
        validate_record_identity(
            self.schema_version,
            FEEDBACK_SCHEMA_VERSION,
            self.feedback_id,
            self.revision,
            self.supersedes_revision,
            _record_error,
        )
        validate_record_content(
            self.target,
            self.verdict,
            self.comment,
            self.comment_redacted,
            self.created_at,
            target_type=FeedbackTarget,
            verdict_type=FeedbackVerdict,
            max_comment_chars=MAX_FEEDBACK_COMMENT_CHARS,
            invalid=_record_error,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "feedback_id": self.feedback_id,
            "revision": self.revision,
            "target": self.target.to_dict(),
            "verdict": self.verdict.value,
            "comment": self.comment,
            "comment_redacted": self.comment_redacted,
            "created_at": self.created_at,
            "supersedes_revision": self.supersedes_revision,
        }

    @classmethod
    def from_dict(cls, value: object) -> "FeedbackRecord":
        if not isinstance(value, dict):
            raise FeedbackError("FEEDBACK_STORE_CORRUPT", "feedback record must be an object")
        try:
            return cls(
                schema_version=value["schema_version"],
                feedback_id=value["feedback_id"],
                revision=value["revision"],
                target=FeedbackTarget.from_dict(value["target"]),
                verdict=FeedbackVerdict(value["verdict"]),
                comment=value.get("comment"),
                comment_redacted=value["comment_redacted"],
                created_at=value["created_at"],
                supersedes_revision=value.get("supersedes_revision"),
            )
        except FeedbackError:
            raise
        except Exception as exc:
            raise FeedbackError("FEEDBACK_STORE_CORRUPT", "feedback record is invalid") from exc


class FeedbackService:
    def __init__(
        self,
        workspace_paths: WorkspacePaths,
        *,
        clock: Callable[[], Any] = default_feedback_clock,
    ) -> None:
        self.workspace_paths = workspace_paths
        self.clock = clock
        self.store = FeedbackStore(workspace_paths.feedback_file, workspace_paths.feedback_lock_file)

    def _created_at(self) -> str:
        return resolve_feedback_time(self.clock(), invalid=_record_error)

    def submit(
        self,
        target: FeedbackTarget,
        verdict: FeedbackVerdict,
        *,
        comment: str | None = None,
    ) -> FeedbackRecord:
        if not isinstance(target, FeedbackTarget) or not isinstance(verdict, FeedbackVerdict):
            raise FeedbackError(FEEDBACK_TARGET_INVALID, "feedback target or verdict is invalid")
        if any(record.target == target for record in self.store.load()):
            raise FeedbackError(FEEDBACK_ALREADY_EXISTS, "feedback already exists for target")
        safe_comment, redacted = normalize_comment(
            comment,
            max_comment_chars=MAX_FEEDBACK_COMMENT_CHARS,
            comment_too_large_reason=FEEDBACK_COMMENT_TOO_LARGE,
            invalid=_record_error,
        )
        record = FeedbackRecord(
            schema_version=FEEDBACK_SCHEMA_VERSION,
            feedback_id="fb-" + uuid4().hex,
            revision=1,
            target=target,
            verdict=verdict,
            comment=safe_comment,
            comment_redacted=redacted,
            created_at=self._created_at(),
            supersedes_revision=None,
        )
        self.store.append(record)
        return record

    def correct(
        self,
        feedback_id: str,
        *,
        expected_revision: int,
        verdict: FeedbackVerdict,
        comment: str | None = None,
    ) -> FeedbackRecord:
        history = self.history(feedback_id)
        latest = history[-1]
        if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision != latest.revision:
            raise FeedbackError(FEEDBACK_REVISION_CONFLICT, "feedback revision is stale")
        if not isinstance(verdict, FeedbackVerdict):
            raise FeedbackError(FEEDBACK_TARGET_INVALID, "feedback verdict is invalid")
        safe_comment, redacted = normalize_comment(
            comment,
            max_comment_chars=MAX_FEEDBACK_COMMENT_CHARS,
            comment_too_large_reason=FEEDBACK_COMMENT_TOO_LARGE,
            invalid=_record_error,
        )
        record = FeedbackRecord(
            schema_version=FEEDBACK_SCHEMA_VERSION,
            feedback_id=latest.feedback_id,
            revision=latest.revision + 1,
            target=latest.target,
            verdict=verdict,
            comment=safe_comment,
            comment_redacted=redacted,
            created_at=self._created_at(),
            supersedes_revision=latest.revision,
        )
        self.store.append(record)
        return record

    def latest(self, feedback_id: str) -> FeedbackRecord:
        history = self.history(feedback_id)
        return history[-1]

    def latest_for_run(self, run_id: str) -> FeedbackRecord | None:
        candidates = [record for record in self.store.load() if record.target.run_id == run_id]
        if not candidates:
            return None
        return max(candidates, key=lambda record: (record.feedback_id, record.revision))

    def history(self, feedback_id: str) -> tuple[FeedbackRecord, ...]:
        values = tuple(record for record in self.store.load() if record.feedback_id == feedback_id)
        if not values:
            raise FeedbackError(FEEDBACK_NOT_FOUND, "feedback was not found")
        return tuple(sorted(values, key=lambda record: record.revision))


__all__ = [
    "FEEDBACK_COMMENT_TOO_LARGE",
    "FEEDBACK_SCHEMA_VERSION",
    "MAX_FEEDBACK_COMMENT_CHARS",
    "MAX_FEEDBACK_DOCUMENT_BYTES",
    "MAX_FEEDBACK_RECORDS",
    "FeedbackError",
    "FeedbackRecord",
    "FeedbackService",
    "FeedbackTarget",
    "FeedbackVerdict",
]

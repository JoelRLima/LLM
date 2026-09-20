"""Atomic, append-history persistence for human feedback."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agent.memory.path_safety import LinkLikePathError, reject_link_like
from agent.runtime.filesystem_primitives import write_bytes_atomic
from agent.runtime.instance_lock import InstanceLock, InstanceLockError

FEEDBACK_SCHEMA_VERSION = 1
MAX_FEEDBACK_COMMENT_CHARS = 2_000
MAX_FEEDBACK_RECORDS = 4_096
MAX_FEEDBACK_DOCUMENT_BYTES = 8 * 1024 * 1024

FEEDBACK_ALREADY_EXISTS = "FEEDBACK_ALREADY_EXISTS"
FEEDBACK_NOT_FOUND = "FEEDBACK_NOT_FOUND"
FEEDBACK_REVISION_CONFLICT = "FEEDBACK_REVISION_CONFLICT"
FEEDBACK_STORE_CORRUPT = "FEEDBACK_STORE_CORRUPT"
FEEDBACK_STORE_LIMIT = "FEEDBACK_STORE_LIMIT"
FEEDBACK_STORE_BUSY = "FEEDBACK_STORE_BUSY"
FEEDBACK_PERSIST_FAILED = "FEEDBACK_PERSIST_FAILED"

if TYPE_CHECKING:
    from agent.evaluation.feedback import FeedbackRecord


class FeedbackStoreError(RuntimeError):
    def __init__(self, reason_code: str, message: str) -> None:
        self.reason_code = reason_code
        super().__init__(message)


def _record_type() -> type[Any]:
    from agent.evaluation.feedback import FeedbackRecord

    return FeedbackRecord


def _record_key(record: Any) -> tuple[str, int]:
    return str(record.feedback_id), int(record.revision)


def _target_key(record: Any) -> tuple[str, str, str, str]:
    target = record.target
    return target.run_id, target.root_task_id, target.terminal_status, target.completed_at


class FeedbackStore:
    def __init__(self, feedback_file: Path, lock_file: Path) -> None:
        self.feedback_file = Path(feedback_file)
        self.lock_file = Path(lock_file)

    def load(self) -> tuple["FeedbackRecord", ...]:
        return self._read_document()

    def append(self, record: "FeedbackRecord") -> None:
        record_type = _record_type()
        if not isinstance(record, record_type):
            raise FeedbackStoreError(FEEDBACK_PERSIST_FAILED, "feedback record has invalid type")
        try:
            lock = InstanceLock.create(self.lock_file)
            lock.acquire()
        except InstanceLockError as exc:
            raise FeedbackStoreError(FEEDBACK_STORE_BUSY, "feedback store is busy") from exc
        try:
            existing = self._read_document()
            self._validate_append(existing, record)
            if len(existing) >= MAX_FEEDBACK_RECORDS:
                raise FeedbackStoreError(FEEDBACK_STORE_LIMIT, "feedback record limit exceeded")
            values = existing + (record,)
            payload = self._encode(values)
            if len(payload) > MAX_FEEDBACK_DOCUMENT_BYTES:
                raise FeedbackStoreError(FEEDBACK_STORE_LIMIT, "feedback document exceeds byte limit")
            try:
                self.feedback_file.parent.mkdir(parents=True, exist_ok=True)
                reject_link_like(self.feedback_file)
                write_bytes_atomic(self.feedback_file, payload)
                reloaded = self._read_document()
            except FeedbackStoreError:
                raise
            except Exception as exc:
                raise FeedbackStoreError(FEEDBACK_PERSIST_FAILED, "feedback write failed") from exc
            if reloaded != values:
                raise FeedbackStoreError(FEEDBACK_PERSIST_FAILED, "feedback write did not round-trip")
        finally:
            lock.release()

    def _read_document(self) -> tuple["FeedbackRecord", ...]:
        try:
            reject_link_like(self.feedback_file)
            if not self.feedback_file.exists():
                return ()
            if self.feedback_file.stat().st_size > MAX_FEEDBACK_DOCUMENT_BYTES:
                raise FeedbackStoreError(FEEDBACK_STORE_LIMIT, "feedback document exceeds byte limit")
            raw = json.loads(self.feedback_file.read_text(encoding="utf-8"))
        except FeedbackStoreError:
            raise
        except (OSError, UnicodeError, LinkLikePathError, json.JSONDecodeError) as exc:
            raise FeedbackStoreError(FEEDBACK_STORE_CORRUPT, "feedback document is unreadable") from exc
        if not isinstance(raw, dict) or raw.get("schema_version") != FEEDBACK_SCHEMA_VERSION:
            raise FeedbackStoreError(FEEDBACK_STORE_CORRUPT, "feedback document schema is invalid")
        raw_records = raw.get("records")
        if not isinstance(raw_records, list):
            raise FeedbackStoreError(FEEDBACK_STORE_CORRUPT, "feedback records are invalid")
        if len(raw_records) > MAX_FEEDBACK_RECORDS:
            raise FeedbackStoreError(FEEDBACK_STORE_LIMIT, "feedback record limit exceeded")
        record_type = _record_type()
        try:
            records = tuple(record_type.from_dict(item) for item in raw_records)
            self._validate_history(records)
            return records
        except FeedbackStoreError:
            raise
        except Exception as exc:
            reason = getattr(exc, "reason_code", FEEDBACK_STORE_CORRUPT)
            raise FeedbackStoreError(reason, "feedback document history is invalid") from exc

    @staticmethod
    def _validate_history(records: Iterable[Any]) -> None:
        values = tuple(records)
        keys: set[tuple[str, int]] = set()
        targets: dict[tuple[str, str, str, str], str] = {}
        chains: dict[str, list[Any]] = {}
        for record in values:
            key = _record_key(record)
            if key in keys:
                raise FeedbackStoreError(FEEDBACK_STORE_CORRUPT, "duplicate feedback revision")
            keys.add(key)
            target = _target_key(record)
            prior_target = targets.get(target)
            if prior_target is not None and prior_target != record.feedback_id:
                raise FeedbackStoreError(FEEDBACK_STORE_CORRUPT, "duplicate feedback target chain")
            targets[target] = record.feedback_id
            chains.setdefault(record.feedback_id, []).append(record)
        for feedback_id, chain in chains.items():
            ordered = sorted(chain, key=lambda item: item.revision)
            if [item.revision for item in ordered] != list(range(1, len(ordered) + 1)):
                raise FeedbackStoreError(FEEDBACK_STORE_CORRUPT, f"feedback revision gap: {feedback_id}")
            target = _target_key(ordered[0])
            if any(_target_key(item) != target for item in ordered):
                raise FeedbackStoreError(FEEDBACK_STORE_CORRUPT, "feedback target changed in history")
            for index, item in enumerate(ordered):
                expected = None if index == 0 else index
                if item.supersedes_revision != expected:
                    raise FeedbackStoreError(FEEDBACK_STORE_CORRUPT, "feedback supersedes chain is invalid")

    @staticmethod
    def _validate_append(existing: tuple[Any, ...], record: Any) -> None:
        FeedbackStore._validate_history(existing)
        same_id = [item for item in existing if item.feedback_id == record.feedback_id]
        same_target = [item for item in existing if _target_key(item) == _target_key(record)]
        if not same_id:
            if record.revision != 1 or record.supersedes_revision is not None:
                raise FeedbackStoreError(FEEDBACK_REVISION_CONFLICT, "new feedback must start at revision one")
            if same_target:
                raise FeedbackStoreError(FEEDBACK_ALREADY_EXISTS, "feedback already exists for target")
            return
        latest = max(same_id, key=lambda item: item.revision)
        if _target_key(latest) != _target_key(record):
            raise FeedbackStoreError(FEEDBACK_STORE_CORRUPT, "feedback target changed")
        if record.revision != latest.revision + 1 or record.supersedes_revision != latest.revision:
            raise FeedbackStoreError(FEEDBACK_REVISION_CONFLICT, "feedback revision is stale")

    @staticmethod
    def _encode(records: tuple[Any, ...]) -> bytes:
        payload = {
            "schema_version": FEEDBACK_SCHEMA_VERSION,
            "records": [record.to_dict() for record in records],
        }
        return (
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("utf-8")


__all__ = [
    "FEEDBACK_ALREADY_EXISTS",
    "FEEDBACK_NOT_FOUND",
    "FEEDBACK_PERSIST_FAILED",
    "FEEDBACK_REVISION_CONFLICT",
    "FEEDBACK_SCHEMA_VERSION",
    "FEEDBACK_STORE_BUSY",
    "FEEDBACK_STORE_CORRUPT",
    "FEEDBACK_STORE_LIMIT",
    "FeedbackStore",
    "FeedbackStoreError",
    "MAX_FEEDBACK_COMMENT_CHARS",
    "MAX_FEEDBACK_DOCUMENT_BYTES",
    "MAX_FEEDBACK_RECORDS",
]

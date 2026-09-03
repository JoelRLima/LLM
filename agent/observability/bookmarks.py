"""Workspace-confined annotations kept separate from semantic trace facts."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent.observability.redaction import canonical_json, redact_text
from agent.observability.trace_store import TraceCorruptError, TraceStore, TraceUnavailableError
from agent.runtime.filesystem_primitives import write_bytes_atomic
from agent.runtime.path_safety import WorkspacePathError, assert_owned_path, assert_path_safe

MAX_BOOKMARK_COUNT = 256
MAX_BOOKMARK_NOTE = 512
BOOKMARK_SCHEMA_VERSION = 1
_BOOKMARK_LOCK = threading.RLock()


@dataclass(frozen=True, slots=True)
class Bookmark:
    run_id: str
    sequence: int
    note: str | None = None
    created_at: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.run_id, str) or not self.run_id.strip():
            raise ValueError("bookmark run_id must be non-empty")
        if isinstance(self.sequence, bool) or not isinstance(self.sequence, int) or self.sequence < 1:
            raise ValueError("bookmark sequence must be a positive integer")
        if self.note is not None:
            if not isinstance(self.note, str):
                raise TypeError("bookmark note must be a string or null")
            object.__setattr__(self, "note", redact_text(self.note, limit=MAX_BOOKMARK_NOTE))
        selected_time = self.created_at or datetime.now(timezone.utc).isoformat()
        if not isinstance(selected_time, str) or not selected_time.strip():
            raise ValueError("bookmark created_at must be non-empty")
        object.__setattr__(self, "created_at", selected_time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": BOOKMARK_SCHEMA_VERSION,
            "run_id": self.run_id,
            "sequence": self.sequence,
            "note": self.note,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, value: Any, *, expected_run_id: str) -> "Bookmark":
        if not isinstance(value, dict) or value.get("run_id") != expected_run_id:
            raise TraceCorruptError("bookmark record is invalid")
        try:
            return cls(
                run_id=expected_run_id,
                sequence=value["sequence"],
                note=value.get("note"),
                created_at=value.get("created_at", ""),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise TraceCorruptError("bookmark record is invalid") from exc


class BookmarkStore:
    """Atomic annotation store under the selected run's owned directory."""

    def __init__(self, workspace_paths: Any) -> None:
        self.workspace_paths = workspace_paths

    def _trace(self, run_id: str) -> TraceStore:
        return TraceStore.open(self.workspace_paths, run_id)

    def _path(self, run_id: str) -> Path:
        trace = self._trace(run_id)
        path = trace.run_dir / "bookmarks.json"
        try:
            assert_owned_path(trace.trace_root, path)
        except WorkspacePathError as exc:
            raise TraceCorruptError("bookmark path escaped trace root") from exc
        return path

    def _load(self, run_id: str) -> list[Bookmark]:
        path = self._path(run_id)
        try:
            inspection = assert_path_safe(path, directory=False)
        except (WorkspacePathError, IsADirectoryError) as exc:
            raise TraceCorruptError("bookmark sidecar is not a regular file") from exc
        if not inspection.exists:
            return []
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise TraceCorruptError("bookmark sidecar cannot be read") from exc
        if (
            not isinstance(raw, dict)
            or raw.get("schema_version") != BOOKMARK_SCHEMA_VERSION
            or raw.get("run_id") != run_id
        ):
            raise TraceCorruptError("bookmark sidecar schema is invalid")
        entries = raw.get("bookmarks")
        if not isinstance(entries, list) or len(entries) > MAX_BOOKMARK_COUNT:
            raise TraceCorruptError("bookmark sidecar bounds are invalid")
        return [Bookmark.from_dict(item, expected_run_id=run_id) for item in entries]

    @staticmethod
    def _write(path: Path, run_id: str, entries: list[Bookmark]) -> None:
        if len(entries) > MAX_BOOKMARK_COUNT:
            raise ValueError("bookmark count exceeds bound")
        payload = {
            "schema_version": BOOKMARK_SCHEMA_VERSION,
            "run_id": run_id,
            "bookmarks": [item.to_dict() for item in sorted(entries, key=lambda item: item.sequence)],
        }
        write_bytes_atomic(path, (canonical_json(payload) + "\n").encode("utf-8"))

    def list(self, run_id: str) -> tuple[Bookmark, ...]:
        with _BOOKMARK_LOCK:
            return tuple(self._load(run_id))

    def add(self, run_id: str, sequence: int, note: str | None = None) -> Bookmark:
        bookmark = Bookmark(run_id, sequence, note)
        with _BOOKMARK_LOCK:
            path = self._path(run_id)
            trace = self._trace(run_id)
            if sequence not in {item.sequence for item in trace.read_result().records}:
                raise TraceUnavailableError("bookmark sequence is not present in the selected trace")
            entries = self._load(run_id)
            updated = [item for item in entries if item.sequence != sequence]
            if len(updated) >= MAX_BOOKMARK_COUNT and all(item.sequence != sequence for item in entries):
                raise ValueError("bookmark count exceeds bound")
            updated.append(bookmark)
            self._write(path, run_id, updated)
        return bookmark

    def remove(self, run_id: str, sequence: int) -> bool:
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
            raise ValueError("bookmark sequence must be a positive integer")
        with _BOOKMARK_LOCK:
            path = self._path(run_id)
            entries = self._load(run_id)
            updated = [item for item in entries if item.sequence != sequence]
            if len(updated) == len(entries):
                return False
            self._write(path, run_id, updated)
            return True

    def reader(self, run_id: str) -> tuple[dict[str, Any], ...]:
        return tuple(item.to_dict() for item in self.list(run_id))


__all__ = [
    "BOOKMARK_SCHEMA_VERSION",
    "MAX_BOOKMARK_COUNT",
    "MAX_BOOKMARK_NOTE",
    "Bookmark",
    "BookmarkStore",
]

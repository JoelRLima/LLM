"""Finite contracts for auxiliary workspace inspection operations."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class BookmarkListRequest:
    run_id: str | None = None


@dataclass(frozen=True, slots=True)
class BookmarkAddRequest:
    run_id: str | None = None
    sequence: int | None = None
    note: str | None = None


@dataclass(frozen=True, slots=True)
class BookmarkRemoveRequest:
    run_id: str | None = None
    sequence: int | None = None


@dataclass(frozen=True, slots=True)
class DiagnosticExportRequest:
    run_id: str | None = None
    output: str | Path | None = None
    force: bool = False
    include_bookmarks: bool = False


@dataclass(frozen=True, slots=True)
class InspectionReadRequest:
    """Transport inputs for one standalone inspection read."""

    run_id: str | None = None
    mode: str = "snapshot"
    limit: int = 100
    after_sequence: int = 0
    selected_sequence: int | None = None
    sequence_start: int | None = None
    sequence_end: int | None = None
    sources: Iterable[str] | None = None
    event_kinds: Iterable[str] | None = None
    activity_categories: Iterable[str] | None = None
    severities: Iterable[str] | None = None
    statuses: Iterable[str] | None = None
    task_id: str | None = None
    root_task_id: str | None = None
    step: int | None = None
    correlation_id: str | None = None
    invocation_id: str | None = None
    time_start: str | None = None
    time_end: str | None = None
    search: str | None = None
    bookmarked_only: bool = False


@dataclass(frozen=True, slots=True)
class InspectionListRequest:
    """Inputs for listing retained runs."""

    limit: int = 100


@dataclass(frozen=True, slots=True)
class BookmarkView:
    schema_version: int
    run_id: str
    sequence: int
    note: str | None
    created_at: str

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "sequence": self.sequence,
            "note": self.note,
            "created_at": self.created_at,
        }


@dataclass(frozen=True, slots=True)
class BookmarkListResult:
    bookmarks: tuple[BookmarkView, ...]

    def to_dict(self) -> dict[str, object]:
        return {"bookmarks": [item.to_dict() for item in self.bookmarks]}


@dataclass(frozen=True, slots=True)
class BookmarkAddResult:
    bookmark: BookmarkView

    def to_dict(self) -> dict[str, object]:
        return {"bookmark": self.bookmark.to_dict()}


@dataclass(frozen=True, slots=True)
class BookmarkRemoveResult:
    removed: bool
    run_id: str
    sequence: int

    def to_dict(self) -> dict[str, object]:
        return {
            "removed": self.removed,
            "run_id": self.run_id,
            "sequence": self.sequence,
        }


@dataclass(frozen=True, slots=True)
class DiagnosticExportResult:
    path: str
    run_id: str
    completeness: str
    sha256: str
    size_bytes: int
    files: tuple[str, ...]
    overwritten: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "run_id": self.run_id,
            "completeness": self.completeness,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "files": list(self.files),
            "overwritten": self.overwritten,
        }

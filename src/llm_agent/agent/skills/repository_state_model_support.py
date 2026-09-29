"""Serialization helpers for the canonical repository-state models."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from typing import Any, Callable, TypeVar

from .repository_state_filesystem_support import _json_text

_SnapshotT = TypeVar("_SnapshotT")


def entry_to_dict(entry: Any) -> dict[str, object]:
    return {
        "path": entry.path,
        "index_status": entry.index_status,
        "worktree_status": entry.worktree_status,
        "untracked": entry.untracked,
    }


def validate_entry(entry: Any) -> None:
    if not isinstance(entry.path, str) or not entry.path or "\x00" in entry.path:
        raise ValueError("repository path is invalid")
    if not isinstance(entry.index_status, str) or not isinstance(
        entry.worktree_status, str
    ):
        raise TypeError("repository status fields must be strings")
    if type(entry.untracked) is not bool:
        raise TypeError("untracked must be boolean")


def _snapshot_entries(raw_entries: Any) -> tuple[Any, ...]:
    from .repository_state import RepositoryStateEntry

    if not isinstance(raw_entries, (list, tuple)):
        raise ValueError("repository snapshot entries must be a list")
    entries: list[Any] = []
    for raw_entry in raw_entries:
        if not isinstance(raw_entry, Mapping):
            raise ValueError("repository entry must be an object")
        if set(raw_entry) != {"path", "index_status", "worktree_status", "untracked"}:
            raise ValueError("repository entry fields are not closed")
        entries.append(
            RepositoryStateEntry(
                raw_entry["path"],
                raw_entry["index_status"],
                raw_entry["worktree_status"],
                raw_entry["untracked"],
            )
        )
    return tuple(entries)


def _snapshot_values(value: Mapping[str, object]) -> tuple[Any, ...]:
    branch = value["branch"]
    head = value["head"]
    reason = value["reason_code"]
    if branch is not None and not isinstance(branch, str):
        raise ValueError("repository branch must be text or null")
    if head is not None and not isinstance(head, str):
        raise ValueError("repository head must be text or null")
    if reason is not None and not isinstance(reason, str):
        raise ValueError("repository reason must be text or null")
    available = value["available"]
    truncated = value["truncated"]
    complete = value["complete"]
    total_entries_observed = value["total_entries_observed"]
    if not isinstance(available, bool):
        raise ValueError("repository available must be boolean")
    if not isinstance(truncated, bool):
        raise ValueError("repository truncated must be boolean")
    if not isinstance(complete, bool):
        raise ValueError("repository complete must be boolean")
    if total_entries_observed is not None and (
        not isinstance(total_entries_observed, int)
        or isinstance(total_entries_observed, bool)
        or total_entries_observed < 0
    ):
        raise ValueError("repository total_entries_observed must be non-negative integer or null")
    return (
        value["available"],
        branch,
        head,
        value["truncated"],
        value["complete"],
        total_entries_observed,
        reason,
    )


def snapshot_from_dict(cls: Callable[..., _SnapshotT], value: Any) -> _SnapshotT:
    """Restore a snapshot while keeping model ownership in the owner module."""

    if not isinstance(value, Mapping):
        raise ValueError("repository snapshot must be an object")
    expected = {
        "available",
        "branch",
        "head",
        "entries",
        "truncated",
        "complete",
        "total_entries_observed",
        "reason_code",
    }
    if set(value) != expected:
        raise ValueError("repository snapshot fields are not closed")
    entries = _snapshot_entries(value["entries"])
    available, branch, head, truncated, complete, total_entries_observed, reason = _snapshot_values(value)
    return cls(
        available,
        branch,
        head,
        entries,
        truncated,
        complete,
        total_entries_observed,
        reason,
    )


def snapshot_to_context_dict(
    snapshot: Any,
    *,
    max_chars: int,
) -> dict[str, object]:
    """Return a bounded metadata-only projection for auxiliary context."""

    limit = max(1, int(max_chars))
    counts = Counter(
        f"{entry.index_status}{entry.worktree_status}"
        for entry in snapshot.entries
    )
    base: dict[str, object] = {
        "available": snapshot.available,
        "branch": snapshot.branch,
        "head": snapshot.head,
        "status_counts": dict(sorted(counts.items())),
        "truncated": snapshot.truncated,
        "complete": snapshot.complete,
        "total_entries_observed": snapshot.total_entries_observed,
        "reason_code": snapshot.reason_code,
        "entries": [],
    }
    represented: list[dict[str, object]] = []
    for entry in snapshot.entries:
        candidate = dict(base)
        candidate["entries"] = [*represented, entry_to_dict(entry)]
        if len(_json_text(candidate)) > limit:
            base["truncated"] = True
            base["complete"] = False
            break
        represented.append(entry_to_dict(entry))
    base["entries"] = represented
    return base


__all__ = [
    "entry_to_dict",
    "snapshot_from_dict",
    "snapshot_to_context_dict",
    "validate_entry",
]

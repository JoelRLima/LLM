"""Workspace-confined path and atomic-write primitives for traces."""

from __future__ import annotations

import hashlib
import re
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from agent.observability.redaction import canonical_json
from agent.runtime.filesystem_primitives import write_bytes_atomic
from agent.runtime.path_safety import WorkspacePathError
from agent.runtime.path_safety import assert_no_link_ancestors as _runtime_assert_no_link_ancestors
from agent.runtime.path_safety import assert_no_link_descendants as _runtime_assert_no_link_descendants
from agent.runtime.path_safety import assert_owned_path as _runtime_assert_owned_path
from agent.runtime.path_safety import assert_path_safe as _runtime_assert_path_safe
from agent.runtime.path_safety import resolve_path as _runtime_resolve_path

TRACE_STORE_SCHEMA_VERSION = 1
_OWNED_RUN_KEY_RE = re.compile(r"run-[0-9a-f]{64}\Z")
_INDEX_LOCK = threading.RLock()


class TraceStoreError(RuntimeError):
    """Base class for explicit trace-store read/write failures."""


class TraceClosedError(TraceStoreError):
    """Raised when a caller writes after the run trace closed."""


class TraceCorruptError(TraceStoreError):
    """Raised when a persisted trace/metadata record cannot be validated."""


class TraceUnavailableError(TraceStoreError):
    """Raised when a selected run is not retained or cannot be opened."""


def _time_text(value: Any = None) -> str:
    from datetime import datetime, timezone

    if value is None:
        return datetime.now(timezone.utc).isoformat()
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str) and value.strip():
        return value
    raise ValueError("trace timestamp must be a non-empty string or datetime")


def safe_run_key(run_id: str) -> str:
    """Encode a run identity without interpolating user-controlled text."""

    if not isinstance(run_id, str) or not run_id.strip():
        raise ValueError("run_id must be a non-empty string")
    return "run-" + hashlib.sha256(run_id.encode("utf-8")).hexdigest()


def _assert_safe_path(path: Path, *, directory: bool | None = None) -> None:
    """Reject link-like final components before touching owned storage."""

    try:
        _runtime_assert_path_safe(path, directory=directory)
    except WorkspacePathError as exc:
        raise TraceStoreError(str(exc)) from exc
    except IsADirectoryError as exc:
        if directory is False:
            raise TraceUnavailableError(str(path)) from exc
        raise


def _assert_no_link_ancestors(path: Path) -> None:
    """Reject link-like existing components before resolving owned storage."""

    try:
        _runtime_assert_no_link_ancestors(path)
    except WorkspacePathError as exc:
        raise TraceStoreError(str(exc)) from exc


def _assert_owned_child(root: Path, child: Path) -> None:
    try:
        _runtime_assert_owned_path(root, child)
    except WorkspacePathError as exc:
        raise TraceStoreError("trace path escaped its owned root") from exc


def _assert_no_link_descendants(root: Path) -> None:
    """Fail closed if a retained run contains a link-like descendant."""

    try:
        _runtime_assert_no_link_descendants(root)
    except WorkspacePathError as exc:
        raise TraceStoreError(str(exc)) from exc
    except OSError as exc:
        raise TraceStoreError(f"cannot inspect trace descendants: {root}") from exc


def _atomic_write(path: Path, document: Mapping[str, Any]) -> None:
    _assert_safe_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _assert_safe_path(path.parent, directory=True)
    encoded = canonical_json(document) + "\n"
    write_bytes_atomic(path, encoded.encode("utf-8"))


def _resolve_trace_root(workspace_paths: Any, *, create: bool = True) -> Path:
    candidate = getattr(workspace_paths, "traces_dir", None)
    if candidate is None:
        candidate = getattr(workspace_paths, "trace_dir", None)
    if candidate is None:
        candidate = getattr(workspace_paths, "trace_root", None)
    if candidate is None:
        candidate = Path(workspace_paths) / "traces"
    try:
        root = _runtime_resolve_path(candidate, reject_link_like=True)
    except WorkspacePathError as exc:
        raise TraceStoreError(str(exc)) from exc
    if create:
        root.mkdir(parents=True, exist_ok=True)
    if root.exists():
        _assert_safe_path(root, directory=True)
    return root


__all__ = [
    "TRACE_STORE_SCHEMA_VERSION",
    "TraceClosedError",
    "TraceCorruptError",
    "TraceStoreError",
    "TraceUnavailableError",
    "_INDEX_LOCK",
    "_OWNED_RUN_KEY_RE",
    "_assert_no_link_ancestors",
    "_assert_no_link_descendants",
    "_assert_owned_child",
    "_assert_safe_path",
    "_atomic_write",
    "_resolve_trace_root",
    "_time_text",
    "safe_run_key",
]

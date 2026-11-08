"""Workspace-bound freshness checks for file-derived memory facts."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from llm_agent.filesystem.path_safety import assert_no_link_ancestors, resolve_workspace_path

_SOURCE_HASH_RE = re.compile(r"^[0-9a-fA-F]{64}$")


def _stored_source_hash(state: Mapping[str, Any], path: str) -> str | None:
    """Return the one current source hash exposed by existing memory state."""

    normalized_path = path.replace("\\", "/").casefold()

    def lookup(mapping: Mapping[str, Any], key: str) -> Any:
        if key in mapping:
            return mapping.get(key)
        for candidate, value in mapping.items():
            if str(candidate).replace("\\", "/").casefold() == normalized_path:
                return value
        return None

    hashes = state.get("file_hashes")
    if isinstance(hashes, Mapping):
        value = lookup(hashes, path)
        return value if isinstance(value, str) and _SOURCE_HASH_RE.fullmatch(value) else None
    cache_entries = state.get("file_cache_entries")
    if isinstance(cache_entries, Mapping):
        entry = lookup(cache_entries, path)
        if isinstance(entry, Mapping):
            value = entry.get("source_hash")
            return value if isinstance(value, str) and _SOURCE_HASH_RE.fullmatch(value) else None
    return None


def _hash_file(path: Path, max_bytes: int | None = None) -> str | None:
    digest = hashlib.sha256()
    total = 0
    with path.open("rb") as stream:
        while True:
            read_size = 1024 * 1024
            if max_bytes is not None:
                read_size = min(read_size, max_bytes - total + 1)
            chunk = stream.read(read_size)
            if not chunk:
                break
            total += len(chunk)
            if max_bytes is not None and total > max_bytes:
                return None
            digest.update(chunk)
    return digest.hexdigest()


def file_fact_freshness(
    state: Mapping[str, Any],
    path: str,
    *,
    workspace_root: str | Path | None = None,
    expected_hash: str | None = None,
    max_bytes: int | None = None,
) -> str:
    """Classify a file-derived memory fact without a model call or mutation.

    ``max_bytes`` lets prompt projection decline oversized files without
    hashing them; callers that need an authoritative freshness result can
    leave it unset.
    """

    stored_hash = expected_hash if expected_hash is not None else _stored_source_hash(state, path)
    if stored_hash is None:
        return "UNVERIFIED_LEGACY_FILE_FACT"
    if workspace_root is None:
        # The legacy public helper has no workspace argument.  Its callers
        # retain the historical projection; W13 callers always provide it.
        return "FRESH_FILE_FACT"
    try:
        root = Path(workspace_root).expanduser().resolve()
        current = resolve_workspace_path(root, path, require_file=True)
        assert_no_link_ancestors(current)
        if max_bytes is not None and current.stat().st_size > max_bytes:
            return "UNVERIFIED_FILE_TOO_LARGE"
        current_hash = _hash_file(current, max_bytes=max_bytes)
    except (OSError, RuntimeError, ValueError):
        return "STALE_OR_INVALID_FILE_FACT"
    if current_hash is None:
        return "UNVERIFIED_FILE_TOO_LARGE"
    return "FRESH_FILE_FACT" if current_hash == stored_hash else "STALE_OR_INVALID_FILE_FACT"


def _include_file_fact(
    state: Mapping[str, Any],
    path: str,
    workspace_root: str | Path | None,
    *,
    max_bytes: int | None = None,
) -> tuple[bool, int]:
    if workspace_root is None:
        return True, 0
    if _stored_source_hash(state, path) is None:
        return False, 0
    try:
        root = Path(workspace_root).expanduser().resolve()
        current = resolve_workspace_path(root, path, require_file=True)
        assert_no_link_ancestors(current)
        size = current.stat().st_size
    except (OSError, RuntimeError, ValueError):
        return False, 0
    if max_bytes is not None and size > max_bytes:
        return False, 0
    freshness = file_fact_freshness(
        state,
        path,
        workspace_root=workspace_root,
        max_bytes=max_bytes,
    )
    return freshness == "FRESH_FILE_FACT", size

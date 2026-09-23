"""Validation for the reviewed optional MCP Windows union lock."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from .lockfiles import LockSummary, LockValidationError, _normalise_name, _parse, _read_lines

_EXACT_VERSION = re.compile(r"[0-9]+(?:\.[0-9]+)*(?:[a-z0-9.-]*)$", re.IGNORECASE)


def validate_mcp_union_lock(
    base_path: Path,
    union_path: Path,
    *,
    required_name: str = "mcp",
    required_version: str = "2.2.0",
) -> LockSummary:
    """Validate that ``union_path`` contains the base lock unchanged plus MCP."""

    # Defer duplicate binary-option diagnostics until after logical-package
    # indexing so duplicate package identity remains the primary structural
    # error for a malformed union document.
    base = _parse(base_path, allow_duplicate_binary_option=True)
    union = _parse(union_path, allow_duplicate_binary_option=True)
    base_map = _entry_map(base, "base")
    union_map = _entry_map(union, "union")
    for name, version in base_map.items():
        if union_map.get(name) != version:
            raise LockValidationError(f"MCP union changed base package {name}")
    required = _normalise_name(required_name)
    if union_map.get(required) != required_version:
        raise LockValidationError(f"MCP union must contain {required_name}=={required_version}")
    for path in (base_path, union_path):
        binary_count = sum(1 for line in _read_lines(path) if line.strip() == "--only-binary :all:")
        if binary_count > 1:
            raise LockValidationError(f"duplicate binary-only option in {path}")
    for entry in union:
        if not _EXACT_VERSION.fullmatch(entry.version):
            raise LockValidationError(f"non-exact version for {entry.name}")
        if len(set(entry.hashes)) != len(entry.hashes):
            raise LockValidationError(f"duplicate hash for {entry.name}")
    return LockSummary(tuple(sorted(union_map)), sum(len(item.hashes) for item in union), 0, True)


def _entry_map(entries: tuple[object, ...], label: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for entry in entries:
        name = _normalise_name(entry.name)  # type: ignore[attr-defined]
        if name in result:
            raise LockValidationError(f"{label} lock contains duplicate logical package {name}")
        result[name] = entry.version  # type: ignore[attr-defined]
    return result


def mcp_union_lock_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


__all__ = ["mcp_union_lock_sha256", "validate_mcp_union_lock"]

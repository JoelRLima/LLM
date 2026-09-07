"""Trusted read-scope discovery for Python target grounding."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterator

from agent.resources.contracts import normalize_resource_id
from agent.runtime.path_safety import WorkspacePathError, assert_path_safe, workspace_relative_path

from . import target_grounding_enumeration as _enumeration
from .intent_admission import AuthorityEnvelope
from .target_grounding_ast import (
    structural_locations_for_source,
    symbol_parts,
)
from .target_grounding_enumeration import _authorized_roots, _inventory_paths
from .target_grounding_limits import (
    DEFAULT_MAX_ENUMERATED_PATHS,
    DEFAULT_MAX_FILES,
    DEFAULT_MAX_TOTAL_SOURCE_BYTES,
    IDENTIFIER_CHUNK_BYTES,
)
from .target_grounding_model import GroundingError, SourceInventoryEntry, SymbolDefinition

os = _enumeration.os


class _SourceByteBudget:
    """Shared actual-byte allowance for one bounded discovery operation."""

    def __init__(self, maximum: int) -> None:
        if maximum <= 0:
            raise GroundingError("GROUNDING_LIMIT_INVALID")
        self.maximum = maximum
        self.consumed = 0

    @property
    def remaining(self) -> int:
        return self.maximum - self.consumed

    def read(self, handle: Any, requested: int) -> bytes:
        if requested <= 0:
            return b""
        remaining = self.remaining
        if remaining <= 0:
            # Do not probe a descriptor after the aggregate allowance is
            # exhausted: the probe itself could consume bytes beyond it.
            raise GroundingError("GROUNDING_DISCOVERY_LIMIT")
        size = min(requested, remaining)
        try:
            chunk = handle.read(size)
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            raise GroundingError("GROUNDING_SOURCE_UNREADABLE") from exc
        if not isinstance(chunk, (bytes, bytearray, memoryview)):
            raise GroundingError("GROUNDING_SOURCE_UNREADABLE")
        actual = bytes(chunk)
        if len(actual) > size:
            # A reader that violates its requested bound cannot be trusted
            # with negative-evidence discovery.
            raise GroundingError("GROUNDING_DISCOVERY_LIMIT")
        self.consumed += len(actual)
        return actual


def iter_source_inventory(
    root: Path,
    envelope: AuthorityEnvelope,
    *,
    max_files: int = DEFAULT_MAX_FILES,
    max_total_source_bytes: int = DEFAULT_MAX_TOTAL_SOURCE_BYTES,
    max_enumerated_paths: int = DEFAULT_MAX_ENUMERATED_PATHS,
) -> Iterator[SourceInventoryEntry]:
    """Stream authorized Python files with independent inventory bounds."""

    if min(max_files, max_total_source_bytes, max_enumerated_paths) <= 0:
        raise GroundingError("GROUNDING_LIMIT_INVALID")
    source_files = 0
    total_source_bytes = 0
    for path in _inventory_paths(
        root,
        envelope,
        max_enumerated_paths=max_enumerated_paths,
    ):
        if path.suffix.casefold() != ".py":
            continue
        try:
            relative = workspace_relative_path(root, path)
            if not envelope.allows_read(relative):
                continue
            assert_path_safe(path, directory=False)
            size = path.stat().st_size
        except WorkspacePathError:
            # A link-like path is not an authorized source candidate.  It is
            # excluded by path confinement, not treated as source evidence.
            continue
        except (OSError, RuntimeError, ValueError) as exc:
            raise GroundingError("GROUNDING_SOURCE_UNREADABLE") from exc
        if size < 0:
            raise GroundingError("GROUNDING_SOURCE_UNREADABLE")
        source_files += 1
        if source_files > max_files:
            raise GroundingError("GROUNDING_DISCOVERY_LIMIT")
        total_source_bytes += size
        if total_source_bytes > max_total_source_bytes:
            raise GroundingError("GROUNDING_DISCOVERY_LIMIT")
        yield SourceInventoryEntry(normalize_resource_id(relative), path, size)


def python_files(
    root: Path,
    envelope: AuthorityEnvelope,
    *,
    max_files: int = DEFAULT_MAX_FILES,
    max_source_bytes: int = 2_000_000,
    max_total_source_bytes: int = DEFAULT_MAX_TOTAL_SOURCE_BYTES,
    max_enumerated_paths: int = DEFAULT_MAX_ENUMERATED_PATHS,
) -> tuple[tuple[str, Path], ...]:
    """Return a deterministic, authorized, bounded Python source inventory."""

    del max_source_bytes
    return tuple(
        (entry.resource, entry.path)
        for entry in iter_source_inventory(
            root,
            envelope,
            max_files=max_files,
            max_total_source_bytes=max_total_source_bytes,
            max_enumerated_paths=max_enumerated_paths,
        )
    )


def _identifier_pattern(identifier: str) -> re.Pattern[bytes]:
    encoded = identifier.encode("utf-8")
    return re.compile(
        rb"(?<![A-Za-z0-9_])" + re.escape(encoded) + rb"(?![A-Za-z0-9_])"
    )


def _bytes_contain_identifier(source: bytes, pattern: re.Pattern[bytes]) -> bool:
    return pattern.search(source) is not None


def _stream_contains_identifier(
    path: Path,
    pattern: re.Pattern[bytes],
    *,
    max_scan_bytes: int,
    byte_budget: _SourceByteBudget | None = None,
) -> bool:
    """Scan an oversized file in bounded chunks, including chunk boundaries."""

    budget = byte_budget or _SourceByteBudget(max_scan_bytes)
    overlap = max(len(pattern.pattern) - 1, 0)
    carry = b""
    try:
        with path.open("rb") as handle:
            while True:
                chunk = budget.read(handle, IDENTIFIER_CHUNK_BYTES)
                if not chunk:
                    return False
                window = carry + chunk
                if _bytes_contain_identifier(window, pattern):
                    return True
                carry = window[-overlap:] if overlap else b""
    except GroundingError:
        raise
    except (OSError, RuntimeError) as exc:
        raise GroundingError("GROUNDING_SOURCE_UNREADABLE") from exc


def _read_or_scan_source(
    path: Path,
    pattern: re.Pattern[bytes],
    *,
    max_source_bytes: int,
    byte_budget: _SourceByteBudget,
) -> bytes | None:
    """Read small sources or scan oversized ones under one shared budget."""

    overlap = max(len(pattern.pattern) - 1, 0)
    collected = bytearray()
    carry = b""
    oversized = False
    try:
        with path.open("rb") as handle:
            while True:
                chunk = byte_budget.read(handle, IDENTIFIER_CHUNK_BYTES)
                if not chunk:
                    return None if oversized else bytes(collected)
                if not oversized and len(collected) + len(chunk) <= max_source_bytes:
                    collected.extend(chunk)
                    continue
                if not oversized:
                    oversized = True
                    window = bytes(collected) + chunk
                else:
                    window = carry + chunk
                if _bytes_contain_identifier(window, pattern):
                    raise GroundingError("GROUNDING_SOURCE_UNCLASSIFIABLE")
                carry = window[-overlap:] if overlap else b""
                collected.clear()
    except GroundingError:
        raise
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise GroundingError("GROUNDING_SOURCE_UNREADABLE") from exc


def _source_sha256(source: bytes) -> str:
    import hashlib

    return hashlib.sha256(source).hexdigest()


def discover_symbol_definitions(
    root: Path,
    envelope: AuthorityEnvelope,
    symbol: str,
    *,
    max_files: int = DEFAULT_MAX_FILES,
    max_source_bytes: int = 2_000_000,
    max_total_source_bytes: int = DEFAULT_MAX_TOTAL_SOURCE_BYTES,
    max_enumerated_paths: int = DEFAULT_MAX_ENUMERATED_PATHS,
    max_candidates: int = 512,
) -> tuple[SymbolDefinition, ...]:
    """Run bounded lexical prefiltering and structural AST classification."""

    if min(
        max_files,
        max_source_bytes,
        max_total_source_bytes,
        max_enumerated_paths,
        max_candidates,
    ) <= 0:
        raise GroundingError("GROUNDING_LIMIT_INVALID")
    parts = symbol_parts(symbol)
    pattern = _identifier_pattern(parts[-1])
    definitions: list[SymbolDefinition] = []
    structural_count = 0
    byte_budget = _SourceByteBudget(max_total_source_bytes)
    for entry in iter_source_inventory(
        root,
        envelope,
        max_files=max_files,
        max_total_source_bytes=max_total_source_bytes,
        max_enumerated_paths=max_enumerated_paths,
    ):
        source = _read_or_scan_source(
            entry.path,
            pattern,
            max_source_bytes=max_source_bytes,
            byte_budget=byte_budget,
        )
        if source is None:
            continue
        if not _bytes_contain_identifier(source, pattern):
            continue
        locations = structural_locations_for_source(source, symbol, entry.resource)
        if not locations:
            continue
        structural_count += len(locations)
        if structural_count > max_candidates:
            raise GroundingError("GROUNDING_CANDIDATE_LIMIT")
        definitions.append(
            SymbolDefinition(
                entry.resource,
                entry.path,
                _source_sha256(source),
                locations,
            )
        )
    return tuple(definitions)


__all__ = [
    "DEFAULT_MAX_ENUMERATED_PATHS",
    "DEFAULT_MAX_FILES",
    "DEFAULT_MAX_TOTAL_SOURCE_BYTES",
    "_authorized_roots",
    "SourceInventoryEntry",
    "SymbolDefinition",
    "discover_symbol_definitions",
    "iter_source_inventory",
    "python_files",
]

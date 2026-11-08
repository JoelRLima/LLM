"""Bounded literal file discovery for the UI-neutral query capability."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Protocol

from llm_agent.application.services.queries import (
    MAX_FIND_FILE_BYTES,
    MAX_FIND_FILES,
    MAX_FIND_MATCHES,
    MAX_FIND_PATTERN_CHARS,
    MAX_FIND_SCAN_CHARS,
    MAX_FIND_SCAN_ENTRIES,
    QUERY_FILE_NOT_UTF8,
    QUERY_IO_FAILED,
    QUERY_PERMISSION_DENIED,
    WorkspaceQueryKind,
    WorkspaceQueryResult,
    _cancel_requested,
    _cancelled,
    _error_text,
    _failed,
    _ResolvedPathError,
    _succeeded,
)
from llm_agent.application.services.query_find_pattern import pattern_error
from llm_agent.application.services.query_utf8 import decode_utf8_prefix


class _Cancellation(Protocol):
    def is_cancelled(self) -> bool: ...


_FIND_SUFFIXES = frozenset({
    ".txt", ".md", ".py", ".json", ".csv", ".log", ".yaml",
    ".yml", ".html", ".css", ".js", ".ts", ".tsx", ".toml",
})


def _scan_directory(
    root: Path,
    pending: list[Path],
    candidates: list[Path],
    scanned_entries: int,
    cancellation: _Cancellation,
    *,
    max_files: int,
    max_scan_entries: int,
) -> tuple[int, bool, bool, bool]:
    incomplete = False
    entries = os.scandir(root)
    with entries:
        for entry in entries:
            if cancellation.is_cancelled():
                return scanned_entries, False, True, incomplete
            scanned_entries += 1
            if scanned_entries > max_scan_entries:
                return scanned_entries, True, False, incomplete
            if entry.name in {".git", ".venv", "__pycache__", "node_modules"} or entry.name.startswith("."):
                continue
            try:
                if entry.is_dir(follow_symlinks=False):
                    pending.append(Path(entry.path))
                elif entry.is_file(follow_symlinks=False):
                    candidates.append(Path(entry.path))
            except OSError:
                incomplete = True
                continue
            if len(candidates) >= max_files:
                return scanned_entries, True, False, incomplete
    return scanned_entries, False, False, incomplete


def find_candidates(
    selected: Path,
    cancellation: _Cancellation,
    *,
    max_files: int,
    max_scan_entries: int,
) -> tuple[list[Path], bool] | None:
    if selected.is_file():
        return [selected], False
    candidates: list[Path] = []
    pending = [selected]
    scanned_entries = 0
    truncated = False
    incomplete = False
    while pending and len(candidates) < max_files:
        if cancellation.is_cancelled():
            return None
        root = pending.pop()
        try:
            scanned_entries, truncated, cancelled, scan_incomplete = _scan_directory(
                root,
                pending,
                candidates,
                scanned_entries,
                cancellation,
                max_files=max_files,
                max_scan_entries=max_scan_entries,
            )
        except OSError:
            if root == selected:
                raise
            incomplete = True
            continue
        incomplete = incomplete or scan_incomplete
        if cancelled:
            return None
        if truncated:
            return candidates, True
    return candidates, truncated or bool(pending) or incomplete


def _line_contains(
    haystack: str,
    target: str,
    cancellation: _Cancellation,
    *,
    max_scan_chars: int,
) -> tuple[bool, bool]:
    overlap = max(0, len(target) - 1)
    for offset in range(0, len(haystack) or 1, max_scan_chars):
        if cancellation.is_cancelled():
            return False, True
        start = max(0, offset - overlap)
        end = min(len(haystack), offset + max_scan_chars)
        if target in haystack[start:end]:
            return True, False
    return False, False


def _match_candidate(
    candidate: Path,
    needle: str,
    matches: list[dict[str, Any]],
    cancellation: _Cancellation,
    *,
    case_sensitive: bool,
    workspace_root: Path,
    resolve_path: Any,
    max_file_bytes: int,
    max_matches: int,
    max_scan_chars: int,
) -> tuple[bool, bool, bool]:
    try:
        relative = candidate.relative_to(workspace_root).as_posix()
        safe = resolve_path(relative)
        with safe.open("rb") as handle:
            payload = handle.read(max_file_bytes + 1)
        file_truncated = len(payload) > max_file_bytes
        text = decode_utf8_prefix(payload[:max_file_bytes], truncated=file_truncated)
    except UnicodeDecodeError:
        raise
    except (OSError, ValueError):
        return True, False, False

    target = needle if case_sensitive else needle.casefold()
    for line_number, line in enumerate(text.splitlines(), 1):
        if cancellation.is_cancelled():
            return file_truncated, True, False
        haystack = line if case_sensitive else line.casefold()
        matched, cancelled = _line_contains(
            haystack,
            target,
            cancellation,
            max_scan_chars=max_scan_chars,
        )
        if cancelled:
            return file_truncated, True, False
        if matched:
            matches.append(
                {
                    "file": relative,
                    "line": line_number,
                    "content": line.strip()[:240],
                }
            )
            if len(matches) >= max_matches:
                return True, False, True
    return file_truncated, False, False


def find_matches(
    candidates: list[Path],
    needle: str,
    *,
    case_sensitive: bool,
    cancellation: _Cancellation,
    workspace_root: Path,
    resolve_path: Any,
    max_files: int,
    max_matches: int,
    max_file_bytes: int,
    max_scan_chars: int,
    fail_on_invalid_utf8: bool = False,
) -> tuple[list[dict[str, Any]], bool, bool]:
    matches: list[dict[str, Any]] = []
    truncated = False
    for candidate in candidates[:max_files]:
        if cancellation.is_cancelled():
            return matches, False, True
        if candidate.suffix.lower() not in _FIND_SUFFIXES:
            continue
        try:
            file_truncated, cancelled, limit_reached = _match_candidate(
                candidate,
                needle,
                matches,
                cancellation,
                case_sensitive=case_sensitive,
                workspace_root=workspace_root,
                resolve_path=resolve_path,
                max_file_bytes=max_file_bytes,
                max_matches=max_matches,
                max_scan_chars=max_scan_chars,
            )
        except UnicodeDecodeError:
            if fail_on_invalid_utf8:
                raise
            truncated = True
            continue
        if cancelled:
            return matches, file_truncated, True
        if limit_reached:
            return matches, True, False
        if file_truncated:
            truncated = True
    return matches, truncated, False


def execute_find(
    service: Any,
    request: Any,
    cancellation: Any,
) -> WorkspaceQueryResult:
    arguments = service._arguments(request, WorkspaceQueryKind.FIND)
    if isinstance(arguments, WorkspaceQueryResult):
        return arguments
    pattern = arguments["pattern"]
    invalid_pattern = pattern_error(request, pattern)
    if invalid_pattern is not None:
        return invalid_pattern
    if _cancel_requested(cancellation):
        return _cancelled(request)
    try:
        selected = service._resolve(arguments["path"])
        candidate_result = find_candidates(
            selected, cancellation, max_files=MAX_FIND_FILES, max_scan_entries=MAX_FIND_SCAN_ENTRIES
        )
        if candidate_result is None:
            return _cancelled(request)
        candidates, candidate_truncated = candidate_result
        matches, match_truncated, cancelled = find_matches(
            candidates,
            pattern,
            case_sensitive=arguments["case_sensitive"],
            cancellation=cancellation,
            workspace_root=service.workspace.root,
            resolve_path=lambda rel: service._resolve(rel, require_file=True),
            max_files=MAX_FIND_FILES,
            max_matches=MAX_FIND_MATCHES,
            max_file_bytes=MAX_FIND_FILE_BYTES,
            max_scan_chars=MAX_FIND_SCAN_CHARS,
            fail_on_invalid_utf8=selected.is_file(),
        )
        if cancelled:
            return _cancelled(request)
        truncated = candidate_truncated or match_truncated
        data = {"matches": matches, "truncated": truncated}
        return _succeeded(request, data, truncated=truncated)
    except _ResolvedPathError as exc:
        return _failed(request, exc.reason_code, _error_text("query find", ValueError(exc.message)))
    except UnicodeDecodeError as exc:
        return _failed(request, QUERY_FILE_NOT_UTF8, _error_text("query find", exc))
    except PermissionError as exc:
        return _failed(request, QUERY_PERMISSION_DENIED, _error_text("query find", exc))
    except (OSError, ValueError) as exc:
        return _failed(request, QUERY_IO_FAILED, _error_text("query find", exc))


__all__ = [
    "MAX_FIND_FILES",
    "MAX_FIND_FILE_BYTES",
    "MAX_FIND_MATCHES",
    "MAX_FIND_PATTERN_CHARS",
    "MAX_FIND_SCAN_CHARS",
    "MAX_FIND_SCAN_ENTRIES",
    "execute_find",
    "find_candidates",
    "find_matches",
]

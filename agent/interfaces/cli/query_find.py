"""Bounded literal file discovery for the interactive query plane."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from threading import Event
from typing import Any

_FIND_SUFFIXES = frozenset(
    {
        ".txt",
        ".md",
        ".py",
        ".json",
        ".csv",
        ".log",
        ".yaml",
        ".yml",
        ".html",
        ".css",
        ".js",
        ".ts",
        ".tsx",
        ".toml",
    }
)


def _scan_directory(
    root: Path,
    pending: list[Path],
    candidates: list[Path],
    scanned_entries: int,
    cancel: Event,
    *,
    max_files: int,
    max_scan_entries: int,
) -> tuple[int, bool, bool]:
    try:
        entries = os.scandir(root)
    except OSError:
        return scanned_entries, False, False
    with entries:
        for entry in entries:
            if cancel.is_set():
                return scanned_entries, False, True
            scanned_entries += 1
            if scanned_entries > max_scan_entries:
                return scanned_entries, True, False
            if entry.name in {".git", ".venv", "__pycache__", "node_modules"} or entry.name.startswith("."):
                continue
            try:
                if entry.is_dir(follow_symlinks=False):
                    pending.append(Path(entry.path))
                elif entry.is_file(follow_symlinks=False):
                    candidates.append(Path(entry.path))
            except OSError:
                continue
            if len(candidates) >= max_files:
                return scanned_entries, True, False
    return scanned_entries, False, False


def find_candidates(
    selected: Path,
    cancel: Event,
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
    while pending and len(candidates) < max_files:
        if cancel.is_set():
            return None
        root = pending.pop()
        scanned_entries, truncated, cancelled = _scan_directory(
            root,
            pending,
            candidates,
            scanned_entries,
            cancel,
            max_files=max_files,
            max_scan_entries=max_scan_entries,
        )
        if cancelled:
            return None
        if truncated:
            return candidates, True
    return candidates, truncated or bool(pending)


def _line_contains(
    haystack: str,
    target: str,
    cancel: Event,
    *,
    max_scan_chars: int,
) -> tuple[bool, bool]:
    overlap = max(0, len(target) - 1)
    for offset in range(0, len(haystack) or 1, max_scan_chars):
        if cancel.is_set():
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
    cancel: Event,
    *,
    case_sensitive: bool,
    workspace_root: Path,
    resolve_path: Callable[[str], Path],
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
        text = payload[:max_file_bytes].decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return False, False, False

    target = needle if case_sensitive else needle.casefold()
    for line_number, line in enumerate(text.splitlines(), 1):
        if cancel.is_set():
            return file_truncated, True, False
        haystack = line if case_sensitive else line.casefold()
        matched, cancelled = _line_contains(
            haystack,
            target,
            cancel,
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
    cancel: Event,
    workspace_root: Path,
    resolve_path: Callable[[str], Path],
    max_files: int,
    max_matches: int,
    max_file_bytes: int,
    max_scan_chars: int,
) -> tuple[list[dict[str, Any]], bool, bool]:
    matches: list[dict[str, Any]] = []
    truncated = False
    for candidate in candidates[:max_files]:
        if cancel.is_set():
            return matches, False, True
        if candidate.suffix.lower() not in _FIND_SUFFIXES:
            continue
        file_truncated, cancelled, limit_reached = _match_candidate(
            candidate,
            needle,
            matches,
            cancel,
            case_sensitive=case_sensitive,
            workspace_root=workspace_root,
            resolve_path=resolve_path,
            max_file_bytes=max_file_bytes,
            max_matches=max_matches,
            max_scan_chars=max_scan_chars,
        )
        if cancelled:
            return matches, file_truncated, True
        if limit_reached:
            return matches, True, False
        if file_truncated:
            truncated = True
    return matches, truncated, False

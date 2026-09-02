"""Filesystem-link preflight used before running project validation."""

from __future__ import annotations

import os
from pathlib import Path

from agent.code.discovery import IGNORED_DIRECTORIES
from agent.runtime.path_safety import is_link_like, resolve_workspace_path


def find_external_link(
    root: Path,
    start: Path | None = None,
    *,
    ignored_directories: frozenset[str] = IGNORED_DIRECTORIES,
    reject_all_links: bool = False,
) -> str | None:
    for current, directory_names, file_names in os.walk(
        start or root, topdown=True, followlinks=False,
    ):
        current_path = Path(current)
        safe_directories: list[str] = []
        for name in sorted(directory_names):
            if name in ignored_directories:
                continue
            candidate = current_path / name
            linked, unsafe = link_status(root, candidate, reject_all_links=reject_all_links)
            if unsafe:
                return candidate.relative_to(root).as_posix()
            if not linked:
                safe_directories.append(name)
        directory_names[:] = safe_directories
        for name in sorted(file_names):
            candidate = current_path / name
            _, unsafe = link_status(root, candidate, reject_all_links=reject_all_links)
            if unsafe:
                return candidate.relative_to(root).as_posix()
    return None


def link_status(root: Path, candidate: Path, *, reject_all_links: bool) -> tuple[bool, bool]:
    linked = is_link_like(candidate)
    if not linked:
        return False, False
    if reject_all_links:
        return True, True
    try:
        resolve_workspace_path(root, candidate)
    except (OSError, ValueError):
        return True, True
    return True, False


__all__ = ["find_external_link", "link_status"]

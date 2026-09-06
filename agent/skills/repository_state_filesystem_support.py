"""Bounded filesystem preflight helpers for repository-state observation."""

from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path
from typing import Any, Mapping

from agent.runtime.filesystem_primitives import has_reparse_point
from agent.runtime.path_safety import WorkspacePathError, assert_owned_path

from .repository_state_support import _RepositoryStateError

MAX_CONFIG_BYTES = 128 * 1024
MAX_METADATA_ENTRIES = 50_000
_SECTION_RE = re.compile(r"^([A-Za-z0-9_.-]+)(?:\s+\"([^\"]*)\")?$")
_KEY_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


def _json_text(value: Mapping[str, object]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _safe_metadata_path(
    git_dir: Path,
    relative: str,
    *,
    required: bool = False,
    directory: bool | None = None,
) -> Path | None:
    current = git_dir
    for component in Path(relative).parts:
        current = current / component
        try:
            metadata = os.lstat(current)
        except FileNotFoundError:
            if required:
                raise _RepositoryStateError("GIT_UNAVAILABLE") from None
            return None
        except OSError as exc:
            raise _RepositoryStateError("UNSAFE_PATH") from exc
        if stat.S_ISLNK(metadata.st_mode) or has_reparse_point(metadata):
            raise _RepositoryStateError("UNSAFE_PATH")
    try:
        selected = assert_owned_path(git_dir, Path(relative))
    except WorkspacePathError as exc:
        raise _RepositoryStateError("GITDIR_OUTSIDE_WORKSPACE") from exc
    except (OSError, RuntimeError, ValueError) as exc:
        raise _RepositoryStateError("GITDIR_OUTSIDE_WORKSPACE") from exc
    if directory is True and not stat.S_ISDIR(metadata.st_mode):
        raise _RepositoryStateError("GIT_UNAVAILABLE")
    if directory is False and not stat.S_ISREG(metadata.st_mode):
        raise _RepositoryStateError("GIT_UNAVAILABLE")
    return selected


def _walk_metadata(
    git_dir: Path,
    relative: str,
    *,
    max_metadata_entries: int = MAX_METADATA_ENTRIES,
) -> None:
    root = _safe_metadata_path(git_dir, relative, required=True, directory=True)
    assert root is not None
    pending = [root]
    observed = 0
    while pending:
        current = pending.pop()
        try:
            children = sorted(
                (Path(entry.path) for entry in os.scandir(current)),
                key=lambda path: path.name,
            )
        except OSError as exc:
            raise _RepositoryStateError("UNSAFE_PATH") from exc
        observed += len(children)
        if observed > max_metadata_entries:
            raise _RepositoryStateError("METADATA_LIMIT")
        for child in children:
            relative_child = child.relative_to(git_dir).as_posix()
            _safe_metadata_path(git_dir, relative_child, required=True)
            try:
                metadata = os.lstat(child)
            except OSError as exc:
                raise _RepositoryStateError("UNSAFE_PATH") from exc
            if stat.S_ISDIR(metadata.st_mode):
                pending.append(child)


def _read_bounded(path: Path, *, limit: int) -> bytes:
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise _RepositoryStateError("GIT_UNAVAILABLE") from exc
    if len(content) > limit:
        raise _RepositoryStateError("METADATA_LIMIT")
    return content


def _config_section(line: str) -> tuple[str, str] | None:
    if not line.startswith("["):
        return None
    if not line.endswith("]"):
        raise _RepositoryStateError("UNSAFE_REPOSITORY_CONFIG")
    match = _SECTION_RE.fullmatch(line[1:-1].strip())
    if match is None:
        raise _RepositoryStateError("UNSAFE_REPOSITORY_CONFIG")
    section_kind = match.group(1).casefold()
    subsection = match.group(2)
    section = section_kind + (
        f' "{subsection.casefold()}"' if subsection is not None else ""
    )
    if section_kind in {"include", "includeif", "filter"}:
        raise _RepositoryStateError("UNSAFE_REPOSITORY_CONFIG")
    return section, section_kind


def _config_assignment(
    line: str,
    section: str,
    section_kind: str,
    seen: set[tuple[str, str]],
) -> None:
    if "=" in line:
        key, value = (part.strip() for part in line.split("=", 1))
    else:
        key, value = line, ""
    if not section or _KEY_RE.fullmatch(key) is None:
        raise _RepositoryStateError("UNSAFE_REPOSITORY_CONFIG")
    key = key.casefold()
    identity = (section, key)
    if identity in seen:
        raise _RepositoryStateError("UNSAFE_REPOSITORY_CONFIG")
    seen.add(identity)
    if section_kind.startswith("filter") or section_kind in {"include", "includeif"}:
        raise _RepositoryStateError("UNSAFE_REPOSITORY_CONFIG")
    if (section_kind, key) in {
        ("core", "worktree"),
        ("core", "hookspath"),
        ("core", "excludesfile"),
        ("diff", "external"),
        ("core", "attributesfile"),
    } and value.strip():
        raise _RepositoryStateError("UNSAFE_REPOSITORY_CONFIG")
    if (section, key) == ("extensions", "worktreeconfig") and value.strip():
        raise _RepositoryStateError("UNSAFE_REPOSITORY_CONFIG")


def _parse_repository_config(content: bytes) -> None:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _RepositoryStateError("UNSAFE_REPOSITORY_CONFIG") from exc
    if "\x00" in text:
        raise _RepositoryStateError("UNSAFE_REPOSITORY_CONFIG")
    section = ""
    section_kind = ""
    seen: set[tuple[str, str]] = set()
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.endswith("\\"):
            raise _RepositoryStateError("UNSAFE_REPOSITORY_CONFIG")
        section_data = _config_section(line)
        if section_data is not None:
            section, section_kind = section_data
            continue
        _config_assignment(line, section, section_kind, seen)


def _resolved_git_directory(workspace: Any) -> tuple[Path, Path]:
    root = workspace.root.resolve()
    if root != workspace.root:
        raise _RepositoryStateError("WORKSPACE_ROOT_MISMATCH")
    git_dir = root / ".git"
    try:
        metadata = os.lstat(git_dir)
    except FileNotFoundError as exc:
        raise _RepositoryStateError("NOT_GIT_REPOSITORY") from exc
    except OSError as exc:
        raise _RepositoryStateError("UNSAFE_PATH") from exc
    if stat.S_ISLNK(metadata.st_mode) or has_reparse_point(metadata):
        raise _RepositoryStateError("GITDIR_OUTSIDE_WORKSPACE")
    if not stat.S_ISDIR(metadata.st_mode):
        raise _RepositoryStateError("GITDIR_OUTSIDE_WORKSPACE")
    try:
        resolved_git = assert_owned_path(root, Path(".git"))
    except (OSError, RuntimeError, ValueError) as exc:
        raise _RepositoryStateError("GITDIR_OUTSIDE_WORKSPACE") from exc
    return root, resolved_git


def _preflight_metadata(git_dir: Path, max_metadata_entries: int) -> None:
    _safe_metadata_path(git_dir, "HEAD", required=True, directory=False)
    _safe_metadata_path(git_dir, "objects", required=True, directory=True)
    _safe_metadata_path(git_dir, "refs", required=True, directory=True)
    for relative, directory in (
        ("index", False),
        ("packed-refs", False),
        ("objects/info", True),
        ("objects/pack", True),
    ):
        _safe_metadata_path(git_dir, relative, directory=directory)
    _walk_metadata(git_dir, "objects", max_metadata_entries=max_metadata_entries)
    _walk_metadata(git_dir, "refs", max_metadata_entries=max_metadata_entries)


def _preflight_config(git_dir: Path) -> None:
    config = _safe_metadata_path(git_dir, "config", required=True, directory=False)
    assert config is not None
    _parse_repository_config(_read_bounded(config, limit=MAX_CONFIG_BYTES))
    config_worktree = _safe_metadata_path(git_dir, "config.worktree")
    if config_worktree is not None:
        raise _RepositoryStateError("UNSAFE_REPOSITORY_CONFIG")
    commondir = _safe_metadata_path(git_dir, "commondir")
    if commondir is not None and _read_bounded(commondir, limit=4096).strip():
        raise _RepositoryStateError("UNSAFE_REPOSITORY_CONFIG")
    alternates = _safe_metadata_path(git_dir, "objects/info/alternates")
    if alternates is not None and _read_bounded(alternates, limit=4096).strip():
        raise _RepositoryStateError("UNSAFE_REPOSITORY_CONFIG")


def _preflight_workspace(
    workspace: Any,
    *,
    max_metadata_entries: int = MAX_METADATA_ENTRIES,
) -> Path:
    _root, git_dir = _resolved_git_directory(workspace)
    _preflight_metadata(git_dir, max_metadata_entries)
    _preflight_config(git_dir)
    return git_dir


__all__ = [
    "MAX_CONFIG_BYTES",
    "MAX_METADATA_ENTRIES",
    "_json_text",
    "_preflight_workspace",
]

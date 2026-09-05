"""Strict porcelain-v2 parsing helpers for repository-state observation."""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent.skills.repository_state import RepositoryStateEntry, RepositoryStateSnapshot

MAX_REPRESENTED_ENTRIES = 256
_OID_RE = re.compile(r"^(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})$")
_MODE_RE = re.compile(r"^[0-7]{6}$")
_STATUS_RE = re.compile(r"^[.MADRCUT]{2}$")
_SUBMODULE_RE = re.compile(r"^(?:N\.\.\.|S[.C][.M][.U])$")
_BRANCH_AB_RE = re.compile(r"^[+-][0-9]+ [+-][0-9]+$")


class _RepositoryStateError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


class _RepositoryStateModels:
    """Late-bound access to the canonical model classes kept in the owner."""

    @staticmethod
    def classes() -> tuple[type[RepositoryStateEntry], type[RepositoryStateSnapshot]]:
        from agent.skills.repository_state import RepositoryStateEntry, RepositoryStateSnapshot

        return RepositoryStateEntry, RepositoryStateSnapshot


if TYPE_CHECKING:
    _EntryFactory = Callable[[str, str, str, bool], RepositoryStateEntry]


def _strict_repository_path(root: Path, path: str) -> str:
    if not path or "\x00" in path:
        raise _RepositoryStateError("MALFORMED_PORCELAIN")
    if path.startswith(("/", "\\")) or (len(path) >= 2 and path[1] == ":"):
        raise _RepositoryStateError("UNSAFE_PATH")
    parts = path.replace("\\", "/").split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise _RepositoryStateError("UNSAFE_PATH")
    try:
        resolved = (root / Path(*parts)).resolve()
        resolved.relative_to(root)
    except (OSError, RuntimeError, ValueError) as exc:
        raise _RepositoryStateError("UNSAFE_PATH") from exc
    return "/".join(parts)


def _validate_oid(value: str, *, allow_initial: bool = False) -> str | None:
    if allow_initial and value == "(initial)":
        return None
    if not _OID_RE.fullmatch(value):
        raise _RepositoryStateError("MALFORMED_PORCELAIN")
    return value


def _parse_header_value(header: str, value: str) -> tuple[str, str | None]:
    if header == "branch.oid":
        return "head", _validate_oid(value, allow_initial=True)
    if header == "branch.head":
        if not value:
            raise _RepositoryStateError("MALFORMED_PORCELAIN")
        return "branch", None if value == "(detached)" else value
    if header == "branch.ab":
        if _BRANCH_AB_RE.fullmatch(value) is None:
            raise _RepositoryStateError("MALFORMED_PORCELAIN")
        return "ignored", None
    if header == "branch.upstream":
        if not value:
            raise _RepositoryStateError("MALFORMED_PORCELAIN")
        return "ignored", None
    raise _RepositoryStateError("MALFORMED_PORCELAIN")


def _parse_header(
    record: str,
    seen_headers: set[str],
) -> tuple[str, str | None] | None:
    if not record.startswith("# "):
        return None
    fields = record.split(" ", 2)
    if len(fields) != 3:
        raise _RepositoryStateError("MALFORMED_PORCELAIN")
    header, value = fields[1], fields[2]
    if header in seen_headers:
        raise _RepositoryStateError("MALFORMED_PORCELAIN")
    seen_headers.add(header)
    return _parse_header_value(header, value)


def _validated_entry_fields(
    record: str,
    *,
    maxsplit: int,
    expected: int,
    mode_end: int,
    oid_start: int,
    oid_end: int,
) -> list[str]:
    fields = record.split(" ", maxsplit)
    if len(fields) != expected or len(fields[1]) != 2 or len(fields[2]) != 4:
        raise _RepositoryStateError("MALFORMED_PORCELAIN")
    if _STATUS_RE.fullmatch(fields[1]) is None or _SUBMODULE_RE.fullmatch(fields[2]) is None:
        raise _RepositoryStateError("MALFORMED_PORCELAIN")
    if any(_MODE_RE.fullmatch(value) is None for value in fields[3:mode_end]):
        raise _RepositoryStateError("MALFORMED_PORCELAIN")
    for value in fields[oid_start:oid_end]:
        _validate_oid(value)
    return fields


def _parse_index_record(root: Path, record: str, entry_factory: _EntryFactory) -> RepositoryStateEntry:
    fields = _validated_entry_fields(
        record,
        maxsplit=8,
        expected=9,
        mode_end=6,
        oid_start=6,
        oid_end=8,
    )
    path = _strict_repository_path(root, fields[8])
    return entry_factory(path, fields[1][0], fields[1][1], False)


def _parse_unmerged_record(root: Path, record: str, entry_factory: _EntryFactory) -> RepositoryStateEntry:
    fields = _validated_entry_fields(
        record,
        maxsplit=10,
        expected=11,
        mode_end=7,
        oid_start=7,
        oid_end=10,
    )
    path = _strict_repository_path(root, fields[10])
    return entry_factory(path, fields[1][0], fields[1][1], False)


def _parse_path_record(
    root: Path,
    record: str,
    entry_factory: _EntryFactory,
    *,
    untracked: bool,
) -> RepositoryStateEntry:
    fields = record.split(" ", 1)
    if len(fields) != 2:
        raise _RepositoryStateError("MALFORMED_PORCELAIN")
    raw_path = fields[1][:-1] if fields[1].endswith("/") else fields[1]
    path = _strict_repository_path(root, raw_path)
    marker = "?" if untracked else "!"
    return entry_factory(path, marker, marker, untracked)


def _parse_entry_record(root: Path, record: str, entry_factory: _EntryFactory) -> RepositoryStateEntry:
    if record.startswith("1 "):
        return _parse_index_record(root, record, entry_factory)
    if record.startswith("u "):
        return _parse_unmerged_record(root, record, entry_factory)
    if record.startswith("? "):
        return _parse_path_record(root, record, entry_factory, untracked=True)
    if record.startswith("! "):
        return _parse_path_record(root, record, entry_factory, untracked=False)
    raise _RepositoryStateError("MALFORMED_PORCELAIN")


def _parse_porcelain_v2(root: Path, raw: bytes) -> RepositoryStateSnapshot:
    entry_type, snapshot_type = _RepositoryStateModels.classes()
    if raw and not raw.endswith(b"\x00"):
        raise _RepositoryStateError("MALFORMED_PORCELAIN")
    records = raw[:-1].split(b"\x00") if raw else []
    branch: str | None = None
    head: str | None = None
    seen_headers: set[str] = set()
    entries: list[RepositoryStateEntry] = []
    total_entries = 0
    for raw_record in records:
        try:
            record = raw_record.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise _RepositoryStateError("MALFORMED_PORCELAIN") from exc
        header = _parse_header(record, seen_headers)
        if header is not None:
            field, value = header
            if field == "branch":
                branch = value
            elif field == "head":
                head = value
            continue
        entry = _parse_entry_record(root, record, entry_type)
        entries.append(entry)
        total_entries += 1
        if len(entries) > MAX_REPRESENTED_ENTRIES:
            entries = entries[:MAX_REPRESENTED_ENTRIES]
    if "branch.oid" not in seen_headers or "branch.head" not in seen_headers:
        raise _RepositoryStateError("MALFORMED_PORCELAIN")
    truncated = total_entries > MAX_REPRESENTED_ENTRIES
    return snapshot_type(
        True,
        branch,
        head,
        tuple(entries),
        truncated,
        not truncated,
        total_entries,
        None,
    )


__all__ = ["MAX_REPRESENTED_ENTRIES", "_RepositoryStateError", "_parse_porcelain_v2"]

"""Strict validation for the hash-bound W18 pip lock files."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .release_identity import BOOTSTRAP_PIP_SHA256, RUNTIME_PINS

_REQUIREMENT = re.compile(r"^([A-Za-z0-9_.-]+)==([^\s]+)(?: \\)?$")
_HASH = re.compile(r"^--hash=sha256:([0-9a-f]{64})(?: \\)?$")


class LockValidationError(ValueError):
    """Raised when a release lock is not hash-bound and binary-only."""


@dataclass(frozen=True)
class LockSummary:
    """Bounded facts extracted from a validated lock."""

    packages: tuple[str, ...]
    hash_count: int
    sdist_count: int
    binary_only: bool


@dataclass
class _Entry:
    name: str
    version: str
    hashes: list[str]


def _normalise_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _read_lines(path: Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise LockValidationError(f"cannot read lock: {path}") from exc


def _close_entry(entries: list[_Entry], current: _Entry | None) -> None:
    if current is None:
        return
    if not current.hashes:
        raise LockValidationError(f"missing hash for {current.name}")
    entries.append(current)


def _consume_line(
    line: str,
    line_number: int,
    entries: list[_Entry],
    current: _Entry | None,
    saw_binary_option: bool,
) -> tuple[_Entry | None, bool]:
    if line == "--only-binary :all:":
        if saw_binary_option:
            raise LockValidationError(f"duplicate binary-only option at line {line_number}")
        return current, True

    requirement = _REQUIREMENT.fullmatch(line)
    if requirement:
        _close_entry(entries, current)
        return _Entry(requirement.group(1), requirement.group(2), []), saw_binary_option

    hash_match = _HASH.fullmatch(line)
    if hash_match and current is not None:
        current.hashes.append(hash_match.group(1))
        return current, saw_binary_option
    if line.startswith("--"):
        raise LockValidationError(f"unsupported lock option at line {line_number}")
    raise LockValidationError(f"invalid lock syntax at line {line_number}")


def _finish_parse(entries: list[_Entry], current: _Entry | None, saw_binary_option: bool) -> tuple[_Entry, ...]:
    _close_entry(entries, current)
    if not saw_binary_option:
        raise LockValidationError("lock must declare --only-binary :all:")
    if not entries:
        raise LockValidationError("lock has no requirements")
    return tuple(entries)


def _parse(path: Path) -> tuple[_Entry, ...]:
    lines = _read_lines(path)
    entries: list[_Entry] = []
    current: _Entry | None = None
    saw_binary_option = False
    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        current, saw_binary_option = _consume_line(
            line,
            line_number,
            entries,
            current,
            saw_binary_option,
        )
    return _finish_parse(entries, current, saw_binary_option)


def validate_bootstrap_lock(path: Path) -> LockSummary:
    """Validate the one-wheel bootstrap pip lock."""

    entries = _parse(path)
    if len(entries) != 1:
        raise LockValidationError("bootstrap lock must contain exactly one requirement")
    entry = entries[0]
    if _normalise_name(entry.name) != "pip" or entry.version != "26.2.1":
        raise LockValidationError("bootstrap lock must pin pip==26.2.1")
    if entry.hashes != [BOOTSTRAP_PIP_SHA256]:
        raise LockValidationError("bootstrap lock must contain only the authorized pip wheel hash")
    return LockSummary(("pip",), 1, 0, True)


def validate_runtime_lock(path: Path) -> LockSummary:
    """Validate exact Phase-0 runtime pins and wheel-only hash coverage."""

    entries = _parse(path)
    expected = {_normalise_name(name): version for name, version in RUNTIME_PINS.items()}
    actual = {_normalise_name(entry.name): entry.version for entry in entries}
    if actual != expected:
        raise LockValidationError(f"runtime pins differ from the frozen Phase 0 set: {actual}")
    if len(actual) != len(entries):
        raise LockValidationError("runtime lock contains duplicate logical packages")
    for entry in entries:
        if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)+(?:[a-z0-9.-]*)", entry.version, re.IGNORECASE):
            raise LockValidationError(f"non-exact version for {entry.name}")
        if not entry.hashes:
            raise LockValidationError(f"missing wheel hash for {entry.name}")
        if len(set(entry.hashes)) != len(entry.hashes):
            raise LockValidationError(f"duplicate hash for {entry.name}")
    return LockSummary(tuple(sorted(actual)), sum(len(entry.hashes) for entry in entries), 0, True)


__all__ = [
    "LockSummary",
    "LockValidationError",
    "validate_bootstrap_lock",
    "validate_runtime_lock",
]

"""Pure User PATH semantics shared by W18 policy tests and acceptance tooling.

The production mutation is intentionally implemented in the PowerShell 5.1
installer.  This module provides a platform-neutral executable model for the
parts that must remain byte-preserving: raw segment order, empty segments,
registry value presence/kind, and case-insensitive Windows equivalence.
"""

from __future__ import annotations

import ntpath
import re
from dataclasses import dataclass
from os import environ
from typing import Mapping


@dataclass(frozen=True)
class PathSnapshot:
    r"""The raw HKCU Environment\Path value and its registry metadata."""

    present: bool
    value: str | None
    kind: str | None = None
    key_present: bool = False


@dataclass(frozen=True)
class PathMutation:
    """A minimal transformation result."""

    changed: bool
    value: str | None
    owned_count: int


def _expand_percent_variables(value: str, environment: Mapping[str, str]) -> str:
    by_lower = {key.casefold(): item for key, item in environment.items()}

    def replace(match: re.Match[str]) -> str:
        return by_lower.get(match.group(1).casefold(), match.group(0))

    return re.sub(r"%([^%]+)%", replace, value)


def comparable_path(value: str | None, environment: Mapping[str, str] | None = None) -> str:
    """Normalize one Windows PATH segment for comparison only."""

    if value is None:
        return ""
    env = environ if environment is None else environment
    text = value.strip()
    if len(text) >= 2 and text.startswith('"') and text.endswith('"'):
        text = text[1:-1].strip()
    text = _expand_percent_variables(text, env)
    if not text:
        return ""
    text = ntpath.normpath(text)
    if len(text) > 3:
        text = text.rstrip("\\/")
    return text.casefold()


def equivalent_segment(
    segment: str,
    owned_path: str,
    environment: Mapping[str, str] | None = None,
) -> bool:
    """Return whether two raw segments identify the same Windows path."""

    left = comparable_path(segment, environment)
    right = comparable_path(owned_path, environment)
    return bool(left) and left == right


def _segments(raw_value: str | None) -> list[str]:
    if not raw_value:
        return []
    return raw_value.split(";")


def add_owned_segment(
    snapshot: PathSnapshot,
    owned_path: str,
    environment: Mapping[str, str] | None = None,
) -> PathMutation:
    """Append one owner, or collapse duplicate equivalent owners."""

    raw = snapshot.value if snapshot.present else None
    segments = _segments(raw)
    matches = [
        index
        for index, segment in enumerate(segments)
        if equivalent_segment(segment, owned_path, environment)
    ]
    if not matches:
        value = owned_path if not raw else f"{raw};{owned_path}"
        return PathMutation(True, value, 1)
    if len(matches) == 1:
        return PathMutation(False, raw, 1)
    kept: list[str] = []
    retained = False
    for segment in segments:
        if equivalent_segment(segment, owned_path, environment):
            if retained:
                continue
            retained = True
        kept.append(segment)
    return PathMutation(True, ";".join(kept), 1)


def remove_owned_segment(
    snapshot: PathSnapshot,
    owned_path: str,
    environment: Mapping[str, str] | None = None,
) -> PathMutation:
    """Remove only equivalent owner segments and preserve every other segment."""

    if not snapshot.present:
        return PathMutation(False, None, 0)
    segments = _segments(snapshot.value)
    kept = [
        segment
        for segment in segments
        if not equivalent_segment(segment, owned_path, environment)
    ]
    owned_count = len(segments) - len(kept)
    if owned_count == 0:
        return PathMutation(False, snapshot.value, 0)
    return PathMutation(True, ";".join(kept), 0)


def reconstructed_persistent_path(
    machine_value: str | None,
    user_snapshot: PathSnapshot,
    environment: Mapping[str, str] | None = None,
) -> str:
    """Build the explicit Machine-then-User PATH used by fresh-shell probes."""

    env = environ if environment is None else environment
    machine = _expand_percent_variables(machine_value or "", env)
    user = _expand_percent_variables(user_snapshot.value or "", env)
    if machine and user:
        return f"{machine};{user}"
    return machine or user


__all__ = [
    "PathMutation",
    "PathSnapshot",
    "add_owned_segment",
    "comparable_path",
    "equivalent_segment",
    "reconstructed_persistent_path",
    "remove_owned_segment",
]

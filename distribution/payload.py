"""Deterministic payload inventory and relocation-safe validation.

The normative inventory schema is ``W18-PAYLOAD-FILES-V1`` and the release
bundle member is ``payload-files.json``; both are intentionally explicit here
so an architecture audit can see the offline payload boundary without loading
task authority.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Iterable, Mapping

from .release_identity import PAYLOAD_INVENTORY_SCHEMA


class PayloadValidationError(ValueError):
    """Raised when a self-contained payload is unsafe or inconsistent."""


CANONICAL_STDLIB_VENV_PREFIX = "runtime/lib/venv"
_CANONICAL_RUNTIME_SCRIPTS = "runtime/scripts"
_CANONICAL_RUNTIME_SCRIPTS_PLACEHOLDER = "runtime/scripts/.empty"
_FORBIDDEN_BUILD_MEMBER_NAMES = frozenset({"bootstrap", "python-bin", "uv.exe", "pip.exe"})


def is_canonical_stdlib_venv_path(value: str) -> bool:
    """Return whether *value* belongs to CPython's canonical stdlib ``venv`` module."""

    path = payload_relative_path(value).casefold()
    return path == CANONICAL_STDLIB_VENV_PREFIX or path.startswith(CANONICAL_STDLIB_VENV_PREFIX + "/")


def _environment_marker_leakage_reason(parts: tuple[str, ...], canonical_stdlib_venv: bool) -> str | None:
    if parts[-1] == "pyvenv.cfg":
        return "pyvenv.cfg is a created virtual-environment marker"
    if ".venv" in parts:
        return ".venv is not an authorized payload location"
    if "venv" in parts and not canonical_stdlib_venv:
        return "venv is outside the canonical CPython stdlib location"
    return None


def _canonical_stdlib_venv_leakage_reason(canonical_stdlib_venv: bool, inventoried: bool) -> str | None:
    if canonical_stdlib_venv and not inventoried:
        return "canonical stdlib venv member is not present in the payload inventory"
    return None


def _forbidden_build_artifact_reason(parts: tuple[str, ...]) -> str | None:
    if any(part in _FORBIDDEN_BUILD_MEMBER_NAMES for part in parts):
        return "build/install tool artifact is forbidden"
    return None


def _payload_layout_leakage_reason(lowered: str) -> str | None:
    if lowered == "scripts" or lowered.startswith("scripts/"):
        return "root Scripts layout is not an authorized payload location"
    if lowered == _CANONICAL_RUNTIME_SCRIPTS:
        return None
    if lowered.startswith(_CANONICAL_RUNTIME_SCRIPTS + "/") and lowered != _CANONICAL_RUNTIME_SCRIPTS_PLACEHOLDER:
        return "runtime/Scripts contains a created-environment layout"
    if lowered == "runtime/bin" or lowered.startswith("runtime/bin/"):
        return "runtime/bin is a created-environment layout"
    if lowered.startswith("bin/") and lowered != "bin/llm-agent.cmd":
        return "root bin contains an unapproved non-product member"
    return None


def payload_path_leakage_reason(value: str, *, inventoried: bool) -> str | None:
    """Return a structural build/install leakage reason, if one exists.

    The CPython stdlib module ``runtime/Lib/venv`` is allowed only when the
    caller proves that the member is part of the canonical payload inventory.
    Created virtual-environment instances remain forbidden.  Exact inventory
    membership and SHA-256/size verification are separate caller obligations.
    """

    path = payload_relative_path(value)
    lowered = path.casefold()
    parts = tuple(lowered.split("/"))
    canonical_stdlib_venv = is_canonical_stdlib_venv_path(path)

    reason = _environment_marker_leakage_reason(parts, canonical_stdlib_venv)
    if reason:
        return reason
    reason = _canonical_stdlib_venv_leakage_reason(canonical_stdlib_venv, inventoried)
    if reason:
        return reason
    reason = _forbidden_build_artifact_reason(parts)
    if reason:
        return reason
    reason = _payload_layout_leakage_reason(lowered)
    if reason:
        return reason
    return None


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def payload_relative_path(value: str) -> str:
    """Validate a POSIX payload path without normalizing unsafe input."""

    if not isinstance(value, str) or not value or "\\" in value or ":" in value or "\x00" in value:
        raise PayloadValidationError(f"unsafe payload path: {value!r}")
    path = PurePosixPath(value)
    windows = PureWindowsPath(value)
    if path.is_absolute() or windows.is_absolute() or windows.drive or windows.anchor:
        raise PayloadValidationError(f"absolute payload path: {value!r}")
    if any(part in {"", ".", ".."} for part in value.split("/")):
        raise PayloadValidationError(f"traversal payload path: {value!r}")
    return value


def _is_reparse_or_link(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError as exc:
        raise PayloadValidationError(f"cannot inspect payload path: {path}") from exc
    if stat.S_ISLNK(info.st_mode):
        return True
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(getattr(info, "st_file_attributes", 0) & reparse)


def _regular_payload_files(root: Path) -> list[tuple[str, Path]]:
    root = root.resolve()
    if not root.is_dir():
        raise PayloadValidationError(f"payload root is not a directory: {root}")
    found: list[tuple[str, Path]] = []
    for directory, child_directories, child_files in os.walk(root, followlinks=False):
        directory_path = Path(directory)
        for name in list(child_directories):
            candidate = directory_path / name
            if _is_reparse_or_link(candidate):
                raise PayloadValidationError(f"payload contains a reparse/link directory: {candidate}")
        for name in child_files:
            candidate = directory_path / name
            if _is_reparse_or_link(candidate) or not candidate.is_file():
                raise PayloadValidationError(f"payload contains a non-regular file: {candidate}")
            relative = candidate.relative_to(root).as_posix()
            found.append((payload_relative_path(relative), candidate))
    found.sort(key=lambda item: item[0])
    seen: set[str] = set()
    for relative, _ in found:
        key = relative.casefold()
        if key in seen:
            raise PayloadValidationError(f"case-colliding payload path: {relative}")
        seen.add(key)
    return found


def make_inventory(root: Path) -> dict[str, Any]:
    """Create the canonical path/size/SHA-256 inventory for a payload root."""

    files = [
        {"path": relative, "size": path.stat().st_size, "sha256": _sha256_file(path)}
        for relative, path in _regular_payload_files(root)
    ]
    return {"schema_version": PAYLOAD_INVENTORY_SCHEMA, "files": files}


def render_inventory(document: Mapping[str, Any]) -> bytes:
    validate_inventory(document)
    return (json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _entry(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {"path", "size", "sha256"}:
        raise PayloadValidationError("payload inventory entry shape is invalid")
    path = payload_relative_path(value["path"])
    size = value["size"]
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        raise PayloadValidationError(f"invalid payload file size: {path}")
    digest = value["sha256"]
    if not isinstance(digest, str) or len(digest) != 64 or digest != digest.lower():
        raise PayloadValidationError(f"invalid payload file hash: {path}")
    try:
        int(digest, 16)
    except ValueError as exc:
        raise PayloadValidationError(f"invalid payload file hash: {path}") from exc
    structural_reason = payload_path_leakage_reason(path, inventoried=True)
    if structural_reason:
        raise PayloadValidationError(f"forbidden payload path: {path} ({structural_reason})")
    return {"path": path, "size": size, "sha256": digest}


def validate_inventory(document: Any, payload_root: Path | None = None) -> Mapping[str, Any]:
    """Validate inventory shape and, optionally, every payload file on disk."""

    if not isinstance(document, Mapping) or set(document) != {"schema_version", "files"}:
        raise PayloadValidationError("payload inventory keys are invalid")
    if document["schema_version"] != PAYLOAD_INVENTORY_SCHEMA:
        raise PayloadValidationError("unknown payload inventory schema")
    files_value = document["files"]
    if not isinstance(files_value, list):
        raise PayloadValidationError("payload inventory files must be an array")
    entries = [_entry(value) for value in files_value]
    paths = [str(entry["path"]) for entry in entries]
    if paths != sorted(paths):
        raise PayloadValidationError("payload inventory paths are not deterministic")
    if len({path.casefold() for path in paths}) != len(paths):
        raise PayloadValidationError("payload inventory has duplicate/case-colliding paths")
    if payload_root is not None:
        actual = dict(_regular_payload_files(payload_root))
        if set(actual) != set(paths):
            raise PayloadValidationError("extracted payload files differ from inventory")
        for entry in entries:
            path = actual[str(entry["path"])]
            if path.stat().st_size != entry["size"] or _sha256_file(path) != entry["sha256"]:
                raise PayloadValidationError(f"payload file hash/size mismatch: {entry['path']}")
    return {"schema_version": document["schema_version"], "files": entries}


def archive_member_paths(names: Iterable[str]) -> tuple[str, ...]:
    """Validate and return sorted regular-file archive member names."""

    result = [payload_relative_path(name) for name in names]
    if len({name.casefold() for name in result}) != len(result):
        raise PayloadValidationError("payload archive has duplicate/case-colliding members")
    return tuple(sorted(result))


__all__ = [
    "CANONICAL_STDLIB_VENV_PREFIX",
    "PayloadValidationError",
    "archive_member_paths",
    "is_canonical_stdlib_venv_path",
    "make_inventory",
    "payload_relative_path",
    "payload_path_leakage_reason",
    "render_inventory",
    "validate_inventory",
]

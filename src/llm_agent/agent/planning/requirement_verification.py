"""Read-only verification of task requirements against workspace files."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from llm_agent.agent.task_definition.verification_model import VerificationCheck

from dataclasses import dataclass

VerificationStatus = Literal["satisfied", "violated", "inconclusive"]

MAX_JSON_BYTES = 1024 * 1024
_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


@dataclass(frozen=True)
class VerificationObservation:
    check_id: str
    status: VerificationStatus
    reason_code: str
    observed_at: str
    evidence_digest: str | None = None


def verify_requirement_checks(
    checks: tuple[VerificationCheck, ...], workspace_root: Path
) -> tuple[VerificationObservation, ...]:
    """Freshly inspect files for declared checks without modifying the workspace."""
    try:
        root = workspace_root.resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        return tuple(
            _observation(check.check_id, "inconclusive", "workspace_unavailable")
            for check in checks
        )
    observations: list[VerificationObservation] = []
    for check in checks:
        observations.append(_verify_one(check, root))
    return tuple(observations)


def _verify_one(check: VerificationCheck, root: Path) -> VerificationObservation:
    try:
        path = _confined_regular_file(root, check.path)
        if path is None:
            return _observation(check.check_id, "violated", "missing_file")
        if check.kind == "file_exists":
            return _observation(check.check_id, "satisfied", "file_exists")
        if check.kind != "json_fields":
            return _observation(check.check_id, "inconclusive", "unsupported_check")
        return _verify_json(check, root, path)
    except FileNotFoundError:
        return _observation(check.check_id, "violated", "missing_file")
    except _UnsafePath:
        return _observation(check.check_id, "violated", "unsafe_path")
    except (ValueError, UnicodeError, json.JSONDecodeError, RecursionError):
        return _observation(check.check_id, "violated", "invalid_json")
    except OSError:
        return _observation(check.check_id, "inconclusive", "filesystem_error")
    except _OversizedFile:
        return _observation(check.check_id, "inconclusive", "file_too_large")


def _verify_json(check: VerificationCheck, root: Path, path: Path) -> VerificationObservation:
    data = _read_bounded(path, root, check.path)
    digest = hashlib.sha256(data).hexdigest()
    try:
        document = json.loads(
            data.decode("utf-8", errors="strict"),
            parse_constant=_reject_constant,
            object_pairs_hook=_unique_object,
        )
    except (ValueError, UnicodeError, json.JSONDecodeError, RecursionError):
        return _observation(check.check_id, "violated", "invalid_json", digest)
    if not isinstance(document, dict):
        return _observation(check.check_id, "violated", "json_not_object", digest)
    if any(field not in document for field in check.required_fields):
        return _observation(check.check_id, "violated", "required_fields_missing", digest)
    return _observation(check.check_id, "satisfied", "required_fields_present", digest)


class _OversizedFile(Exception):
    pass


class _UnsafePath(Exception):
    pass


def _confined_regular_file(root: Path, relative: str) -> Path | None:
    components = relative.split("/")
    if (
        not relative
        or "\\" in relative
        or ":" in relative
        or Path(relative).is_absolute()
        or any(component in ("", ".", "..") for component in components)
    ):
        raise _UnsafePath
    candidate = root.joinpath(*relative.split("/"))
    current = root
    for component in components:
        current = current / component
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            return None
        if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, "st_file_attributes", 0) & _REPARSE_POINT:
            raise OSError("reparse point")
    try:
        candidate.resolve(strict=True).relative_to(root)
    except (ValueError, FileNotFoundError) as error:
        raise OSError("path escape") from error
    metadata = candidate.stat()
    if not stat.S_ISREG(metadata.st_mode):
        raise OSError("not a regular file")
    return candidate


def _read_bounded(path: Path, root: Path, relative: str) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "rb") as stream:
        opened = os.fstat(stream.fileno())
        if not stat.S_ISREG(opened.st_mode):
            raise OSError("not a regular file")
        _verify_opened_target(path, root, relative, opened)
        data = stream.read(MAX_JSON_BYTES + 1)
    if len(data) > MAX_JSON_BYTES:
        raise _OversizedFile
    return data


def _verify_opened_target(path: Path, root: Path, relative: str, opened: os.stat_result) -> None:
    current = root
    for component in relative.split("/"):
        current = current / component
        metadata = current.lstat()
        if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, "st_file_attributes", 0) & _REPARSE_POINT:
            raise OSError("reparse point")
    current_stat = path.stat()
    if (opened.st_dev, opened.st_ino) != (current_stat.st_dev, current_stat.st_ino):
        raise OSError("target changed during open")


def _reject_constant(value: str) -> Any:
    raise ValueError(f"invalid JSON constant: {value}")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _observation(
    check_id: str, status: VerificationStatus, reason_code: str, digest: str | None = None
) -> VerificationObservation:
    return VerificationObservation(
        check_id=check_id,
        status=status,
        reason_code=reason_code,
        observed_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        evidence_digest=digest,
    )

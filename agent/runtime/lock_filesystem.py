"""Filesystem boundaries used by the workspace instance lock."""

from __future__ import annotations

import os
import stat
from pathlib import Path

from agent.runtime.file_lock import OPEN_BINARY, read_descriptor

_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


class UnsafeLockPathError(OSError):
    """Raised when a final lock entry is not a safe regular file."""


def _has_reparse_point(stat_result: os.stat_result) -> bool:
    attributes = getattr(stat_result, "st_file_attributes", 0)
    return bool(attributes & _REPARSE_POINT)


def is_safe_regular(stat_result: os.stat_result) -> bool:
    """Return whether a path/descriptor identity is a non-reparse regular file."""

    return (
        stat.S_ISREG(stat_result.st_mode)
        and not stat.S_ISLNK(stat_result.st_mode)
        and not _has_reparse_point(stat_result)
    )


def validate_final_entry(path: Path, *, allow_missing: bool) -> os.stat_result | None:
    """Validate the lexical final entry without following it."""

    try:
        stat_result = os.lstat(path)
    except FileNotFoundError:
        if allow_missing:
            return None
        raise
    if not is_safe_regular(stat_result):
        raise UnsafeLockPathError(f"unsafe final lock entry: {path}")
    return stat_result


def descriptor_matches_path(path: Path, descriptor: int) -> bool:
    """Compare an opened descriptor with the lexical final path entry."""

    try:
        path_stat = os.lstat(path)
        descriptor_stat = os.fstat(descriptor)
    except OSError:
        return False
    return (
        is_safe_regular(path_stat)
        and is_safe_regular(descriptor_stat)
        and os.path.samestat(path_stat, descriptor_stat)
    )


def open_verified(path: Path, flags: int, mode: int = 0o600) -> int:
    """Open a lock entry and verify its descriptor before the caller mutates it."""

    validate_final_entry(path, allow_missing=bool(flags & os.O_CREAT))
    if os.name != "nt":
        flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, mode)
    try:
        if not descriptor_matches_path(path, descriptor):
            raise UnsafeLockPathError(f"lock entry changed during open: {path}")
        return descriptor
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise


def unlink_if_same_stat(path: Path, expected_stat: os.stat_result) -> bool:
    """Unlink a safe final entry only when it still has the expected identity."""

    try:
        current = validate_final_entry(path, allow_missing=False)
        if current is None or not os.path.samestat(current, expected_stat):
            return False
        os.unlink(path)
    except (OSError, UnsafeLockPathError):
        return False
    return True


def unlink_if_observed(path: Path, observed_stat: os.stat_result, observed_raw: bytes) -> bool:
    """Revalidate, compare bytes, and unlink one observed regular lock entry."""

    descriptor: int | None = None
    try:
        descriptor = open_verified(path, OPEN_BINARY | os.O_RDONLY)
        if not os.path.samestat(os.fstat(descriptor), observed_stat):
            return False
        if read_descriptor(descriptor, max_bytes=len(observed_raw) + 1) != observed_raw:
            return False
        if not descriptor_matches_path(path, descriptor):
            return False
    except (OSError, UnsafeLockPathError):
        return False
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
    try:
        current = validate_final_entry(path, allow_missing=False)
        if current is None or not os.path.samestat(current, observed_stat):
            return False
        os.unlink(path)
        return True
    except (OSError, UnsafeLockPathError):
        return False


def sync_parent_directory(path: Path) -> None:
    """Persist a directory-entry publication where the platform exposes it."""

    if os.name == "nt":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path.parent, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


__all__ = [
    "UnsafeLockPathError",
    "descriptor_matches_path",
    "is_safe_regular",
    "open_verified",
    "sync_parent_directory",
    "unlink_if_observed",
    "unlink_if_same_stat",
    "validate_final_entry",
]

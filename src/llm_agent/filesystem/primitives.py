"""Exact filesystem mechanics shared by distinct higher-level policies."""

from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path
from typing import Callable

WINDOWS_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x0400)


def has_reparse_point(stat_result: os.stat_result) -> bool:
    return bool(getattr(stat_result, "st_file_attributes", 0) & WINDOWS_REPARSE_POINT)


def is_link_like(path: str | Path) -> bool:
    """Detect a symlink/junction/reparse final component without following it."""

    try:
        metadata = os.lstat(Path(path))
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return stat.S_ISLNK(metadata.st_mode) or has_reparse_point(metadata)


class FinalPathInspection:
    """No-follow metadata for one final filesystem component."""

    __slots__ = ("exists", "is_link_like", "link_kind", "metadata")

    def __init__(
        self,
        *,
        exists: bool,
        is_link_like: bool,
        link_kind: str | None = None,
        metadata: os.stat_result | None = None,
    ) -> None:
        self.exists = exists
        self.is_link_like = is_link_like
        self.link_kind = link_kind
        self.metadata = metadata

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, FinalPathInspection):
            return NotImplemented
        return (
            self.exists,
            self.is_link_like,
            self.link_kind,
            self.metadata,
        ) == (
            other.exists,
            other.is_link_like,
            other.link_kind,
            other.metadata,
        )

    def __repr__(self) -> str:
        return (
            "FinalPathInspection("
            f"exists={self.exists!r}, is_link_like={self.is_link_like!r}, "
            f"link_kind={self.link_kind!r}, metadata={self.metadata!r})"
        )


def inspect_final_path(
    path: str | Path,
    *,
    lstat: Callable[[str | os.PathLike[str]], os.stat_result] | None = None,
) -> FinalPathInspection:
    """Inspect the lexical final component while preserving linked ancestors."""

    try:
        metadata = (lstat or os.lstat)(Path(path))
    except FileNotFoundError:
        return FinalPathInspection(exists=False, is_link_like=False)
    if stat.S_ISLNK(metadata.st_mode):
        return FinalPathInspection(
            exists=True,
            is_link_like=True,
            link_kind="link simbólico",
            metadata=metadata,
        )
    if has_reparse_point(metadata):
        return FinalPathInspection(
            exists=True,
            is_link_like=True,
            link_kind="ponto de reparse do Windows",
            metadata=metadata,
        )
    return FinalPathInspection(exists=True, is_link_like=False, metadata=metadata)


def sync_parent_directory(path: str | Path) -> None:
    """Persist a directory-entry publication where the platform exposes it."""

    if os.name == "nt":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(Path(path).parent, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_bytes_atomic(path: str | Path, content: bytes) -> None:
    """Publish already-serialized bytes through one durable atomic replacement."""

    if not isinstance(content, bytes):
        raise TypeError("content must be bytes")
    destination = Path(path)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary_path = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, destination)
        temporary_path = None
        sync_parent_directory(destination)
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass


__all__ = [
    "FinalPathInspection",
    "WINDOWS_REPARSE_POINT",
    "has_reparse_point",
    "inspect_final_path",
    "is_link_like",
    "sync_parent_directory",
    "write_bytes_atomic",
]

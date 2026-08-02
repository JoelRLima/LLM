"""Fail-closed checks for link-like persistent-memory endpoints."""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path

_WINDOWS_REPARSE_POINT = getattr(
    stat,
    "FILE_ATTRIBUTE_REPARSE_POINT",
    0x0400,
)


@dataclass(frozen=True)
class FinalPathInspection:
    """Metadata for one final path component, obtained without following it."""

    exists: bool
    is_link_like: bool
    link_kind: str | None = None
    metadata: os.stat_result | None = None


class LinkLikePathError(RuntimeError):
    """Raised when durable state points at a symlink or Windows reparse point."""

    def __init__(self, path: Path, kind: str) -> None:
        self.path = path
        self.kind = kind
        super().__init__(
            f"O caminho persistente {path} usa {kind}; "
            "links não são aceitos para este artefato."
        )


def inspect_final_path(path: str | Path) -> FinalPathInspection:
    """Inspect only the final component, preserving legitimate linked ancestors."""

    candidate = Path(path)
    try:
        metadata = os.lstat(candidate)
    except FileNotFoundError:
        return FinalPathInspection(exists=False, is_link_like=False)

    if stat.S_ISLNK(metadata.st_mode):
        return FinalPathInspection(
            exists=True,
            is_link_like=True,
            link_kind="link simbólico",
            metadata=metadata,
        )

    file_attributes = int(getattr(metadata, "st_file_attributes", 0))
    if file_attributes & _WINDOWS_REPARSE_POINT:
        return FinalPathInspection(
            exists=True,
            is_link_like=True,
            link_kind="ponto de reparse do Windows",
            metadata=metadata,
        )

    return FinalPathInspection(
        exists=True,
        is_link_like=False,
        metadata=metadata,
    )


def reject_link_like(path: str | Path) -> FinalPathInspection:
    """Return no-follow metadata or reject a link-like final component."""

    candidate = Path(path)
    inspection = inspect_final_path(candidate)
    if inspection.is_link_like:
        raise LinkLikePathError(
            candidate,
            inspection.link_kind or "link",
        )
    return inspection


__all__ = [
    "FinalPathInspection",
    "LinkLikePathError",
    "inspect_final_path",
    "reject_link_like",
]

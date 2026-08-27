"""Fail-closed checks for link-like persistent-memory endpoints."""

from __future__ import annotations

import os
from pathlib import Path

from agent.runtime.filesystem_primitives import (
    FinalPathInspection,
)
from agent.runtime.filesystem_primitives import (
    inspect_final_path as _inspect_final_path,
)


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

    # Keep the module-local lstat seam used by platform regression tests while
    # delegating the exact link/reparse mechanics to the shared primitive.
    return _inspect_final_path(path, lstat=os.lstat)


def reject_link_like(path: str | Path) -> FinalPathInspection:
    """Return no-follow metadata or reject a link-like final component."""

    candidate = Path(path)
    inspection = inspect_final_path(candidate)
    if inspection.is_link_like:
        raise LinkLikePathError(candidate, inspection.link_kind or "link")
    return inspection


__all__ = [
    "FinalPathInspection",
    "LinkLikePathError",
    "inspect_final_path",
    "reject_link_like",
]

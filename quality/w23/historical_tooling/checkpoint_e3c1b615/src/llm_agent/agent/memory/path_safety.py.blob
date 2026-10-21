"""Direct compatibility facade for durable path safety."""

import os
from pathlib import Path

from llm_agent.filesystem.path_safety import (
    FinalPathInspection,
    LinkLikePathError,
    WorkspacePathError,
    reject_link_like,
)
from llm_agent.filesystem.path_safety import (
    inspect_final_path as _inspect_final_path,
)


def inspect_final_path(path: str | Path) -> FinalPathInspection:
    """Keep the historical lstat seam while using the Platform owner."""

    return _inspect_final_path(path, lstat=os.lstat)


__all__ = [
    "FinalPathInspection",
    "LinkLikePathError",
    "WorkspacePathError",
    "inspect_final_path",
    "reject_link_like",
]

"""Explicit package compatibility facade for the canonical runtime path owner.

Repository-controlled productive code imports ``agent.runtime.path_safety``
directly. This shipped submodule remains only as a narrow import projection;
it contains no path-resolution or confinement logic.
"""

from __future__ import annotations

from agent.runtime.path_safety import (
    WorkspacePathError,
    is_link_like,
    resolve_workspace_path,
    workspace_command_argument,
    workspace_relative_path,
)

__all__ = [
    "WorkspacePathError",
    "is_link_like",
    "resolve_workspace_path",
    "workspace_command_argument",
    "workspace_relative_path",
]

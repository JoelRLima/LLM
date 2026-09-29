"""Platform-owned filesystem and path-safety primitives."""

from .path_safety import LinkLikePathError, WorkspacePathError, reject_link_like
from .primitives import FinalPathInspection, inspect_final_path, write_bytes_atomic

__all__ = [
    "FinalPathInspection",
    "LinkLikePathError",
    "WorkspacePathError",
    "inspect_final_path",
    "reject_link_like",
    "write_bytes_atomic",
]

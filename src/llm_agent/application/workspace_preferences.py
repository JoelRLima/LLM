"""Shared persistence of the last selected project, independent of its picker UI."""
from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from llm_agent.application import workspace_recents
from llm_agent.application.context import AppPaths, WorkspaceContext
from llm_agent.application.persistence import AtomicJsonWriteError, write_json_atomic


def load_last_workspace(app_paths: AppPaths) -> Path | None:
    path = getattr(app_paths, "last_workspace_file", None)
    if path is None:
        return None
    path = Path(path)
    try:
        if path.is_symlink() or not path.is_file():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        candidate = payload.get("workspace") if isinstance(payload, dict) else None
        if not isinstance(candidate, str) or not candidate.strip():
            return None
        return cast(Path, WorkspaceContext.create(candidate).root)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None


def remember_workspace(app_paths: AppPaths, workspace: str | Path) -> None:
    path = getattr(app_paths, "last_workspace_file", None)
    if path is None:
        return
    try:
        root = WorkspaceContext.create(workspace).root
        write_json_atomic(Path(path), {"schema_version": 1, "workspace": str(root)})
        workspace_recents.remember_recent_workspace(app_paths, root)
    except (AtomicJsonWriteError, OSError, TypeError, ValueError):
        # Optional persistence cannot prevent a selected project from opening.
        return

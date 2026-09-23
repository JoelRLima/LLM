"""Canonical bounded recent-workspace document owner."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from agent.memory.json_persistence import read_json_object, write_text_atomic
from agent.runtime.instance_lock import InstanceLock
from agent.runtime.workspace_context import WorkspaceContext

MAX_RECENT_WORKSPACES = 8
MAX_WORKSPACE_PATH_BYTES = 4096


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("invalid recent workspace timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    offset = parsed.utcoffset()
    if parsed.tzinfo is None or offset is None or offset.total_seconds() != 0:
        raise ValueError("recent workspace timestamp must be UTC")
    return parsed.astimezone(timezone.utc)


def _validated_workspace(value: object) -> Path | None:
    if not isinstance(value, str) or not value.strip() or len(value.encode("utf-8")) > MAX_WORKSPACE_PATH_BYTES:
        return None
    try:
        candidate = Path(value).expanduser()
        if candidate.is_symlink():
            return None
        return cast(Path, WorkspaceContext.create(value).root)
    except (OSError, TypeError, ValueError):
        return None


def _records(path: Path) -> list[dict[str, object]]:
    payload = read_json_object(path, missing_ok=True)
    if payload is None or payload.get("schema_version") != 1 or not isinstance(payload.get("items"), list):
        return []
    merged: dict[str, tuple[Path, datetime]] = {}
    for item in payload["items"][:MAX_RECENT_WORKSPACES * 2]:
        if not isinstance(item, dict):
            continue
        root = _validated_workspace(item.get("path"))
        if root is None:
            continue
        try:
            stamp = _timestamp(item.get("last_opened_utc"))
        except (TypeError, ValueError):
            continue
        previous = merged.get(str(root))
        if previous is None or stamp > previous[1]:
            merged[str(root)] = (root, stamp)
    return [
        {"path": str(root), "last_opened_utc": stamp.isoformat().replace("+00:00", "Z")}
        for root, stamp in sorted(merged.values(), key=lambda item: (-item[1].timestamp(), str(item[0])))[:MAX_RECENT_WORKSPACES]
    ]


def load_recent_workspaces(app_paths: Any, *, limit: int = MAX_RECENT_WORKSPACES) -> tuple[Path, ...]:
    raw_path = getattr(app_paths, "recent_workspaces_file", None)
    if raw_path is None:
        return ()
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
        raise ValueError("recent workspace limit is invalid")
    try:
        return tuple(
            Path(cast(str, item["path"]))
            for item in _records(Path(raw_path))[: min(limit, MAX_RECENT_WORKSPACES)]
        )
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return ()


def remember_recent_workspace(app_paths: Any, workspace: str | Path) -> None:
    raw_path = getattr(app_paths, "recent_workspaces_file", None)
    if raw_path is None:
        return
    path = Path(raw_path)
    try:
        root = WorkspaceContext.create(workspace).root
        current = [item for item in _records(path) if item["path"] != str(root)]
        current.insert(0, {"path": str(root), "last_opened_utc": _now()})
        path.parent.mkdir(parents=True, exist_ok=True)
        with InstanceLock.create(path.with_name(f"{path.name}.lock"), create_parent=False):
            write_text_atomic(
                path,
                json.dumps({"schema_version": 1, "items": current[:MAX_RECENT_WORKSPACES]}, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
                create_parent=False,
            )
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return


__all__ = ["MAX_RECENT_WORKSPACES", "MAX_WORKSPACE_PATH_BYTES", "load_recent_workspaces", "remember_recent_workspace"]

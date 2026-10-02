"""Application owner for the bounded recent-workspace history."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from llm_agent.agent.runtime.instance_lock import InstanceLock, InstanceLockError
from llm_agent.application.context import AppPaths, WorkspaceContext
from llm_agent.storage.json_persistence import (
    AtomicWriteError,
    JsonObjectReadError,
    read_json_object,
    write_text_atomic,
)

_MAX_RECENT_WORKSPACES = 8
_MAX_WORKSPACE_PATH_BYTES = 4096


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
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value.encode("utf-8")) > _MAX_WORKSPACE_PATH_BYTES
    ):
        return None
    try:
        candidate = Path(value).expanduser()
        if candidate.is_symlink():
            return None
        root = WorkspaceContext.create(value).root
        return root if isinstance(root, Path) else None
    except (OSError, TypeError, ValueError):
        return None


def _records(path: Path) -> list[dict[str, object]]:
    payload = read_json_object(path, missing_ok=True)
    if payload is None:
        return []
    version = payload.get("schema_version")
    items = payload.get("items")
    if type(version) is not int or version != 1 or not isinstance(items, list):
        return []

    merged: dict[str, tuple[Path, datetime]] = {}
    for item in items[: _MAX_RECENT_WORKSPACES * 2]:
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

    ordered = sorted(
        merged.values(),
        key=lambda item: (-item[1].timestamp(), str(item[0])),
    )[:_MAX_RECENT_WORKSPACES]
    return [
        {
            "path": str(root),
            "last_opened_utc": stamp.isoformat().replace("+00:00", "Z"),
        }
        for root, stamp in ordered
    ]


def list_recent_workspaces(
    app_paths: AppPaths,
    *,
    limit: int = _MAX_RECENT_WORKSPACES,
) -> tuple[Path, ...]:
    """Return validated recent workspaces, newest first, within the caller limit."""

    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
        raise ValueError("recent workspace limit is invalid")
    try:
        records = _records(app_paths.recent_workspaces_file)
        roots: list[Path] = []
        for item in records[: min(limit, _MAX_RECENT_WORKSPACES)]:
            path_value = item.get("path")
            if isinstance(path_value, str):
                roots.append(Path(path_value))
        return tuple(roots)
    except (JsonObjectReadError, OSError, TypeError, ValueError, json.JSONDecodeError):
        return ()


def remember_recent_workspace(app_paths: AppPaths, workspace: str | Path) -> None:
    """Best-effort persist a successfully opened workspace in bounded history."""

    path = app_paths.recent_workspaces_file
    try:
        root = WorkspaceContext.create(workspace).root
        current = [item for item in _records(path) if item.get("path") != str(root)]
        current.insert(0, {"path": str(root), "last_opened_utc": _now()})
        path.parent.mkdir(parents=True, exist_ok=True)
        with InstanceLock.create(
            path.with_name(f"{path.name}.lock"),
            create_parent=False,
        ):
            write_text_atomic(
                path,
                json.dumps(
                    {
                        "schema_version": 1,
                        "items": current[:_MAX_RECENT_WORKSPACES],
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n",
                create_parent=False,
            )
    except (
        JsonObjectReadError,
        AtomicWriteError,
        InstanceLockError,
        OSError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
    ):
        return


__all__ = ["list_recent_workspaces", "remember_recent_workspace"]

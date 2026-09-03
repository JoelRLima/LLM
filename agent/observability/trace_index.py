"""Atomic persistence of the retained-run index for trace writers."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from agent.observability.trace_paths import (
    _INDEX_LOCK,
    TRACE_STORE_SCHEMA_VERSION,
    TraceCorruptError,
    _assert_safe_path,
)


def update_index(
    *,
    index_file: Path,
    run_key: str,
    run_id: str,
    root_task_id: str,
    metadata: Mapping[str, Any],
    atomic_write: Callable[[Path, Mapping[str, Any]], None],
) -> None:
    """Merge one writer's persisted metadata into the workspace index."""

    with _INDEX_LOCK:
        _assert_safe_path(index_file)
        runs: dict[str, Any] = {}
        if index_file.exists():
            try:
                loaded = json.loads(index_file.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, ValueError) as exc:
                raise TraceCorruptError("trace index cannot be read") from exc
            if not isinstance(loaded, Mapping) or not isinstance(loaded.get("runs"), Mapping):
                raise TraceCorruptError("trace index is invalid")
            if any(not isinstance(key, str) or not isinstance(value, Mapping) for key, value in loaded["runs"].items()):
                raise TraceCorruptError("trace index contains invalid run summaries")
            runs = dict(loaded["runs"])
        runs[run_key] = {
            "run_key": run_key,
            "run_id": run_id,
            "root_task_id": root_task_id,
            "start_time": metadata["start_time"],
            "end_time": metadata["end_time"],
            "active": metadata["active"],
            "completeness": metadata["completeness"],
            "observability_mode": metadata["observability_mode"],
            "highest_sequence": metadata["highest_sequence_persisted"],
            "final_outcome": metadata["final_outcome"],
        }
        atomic_write(index_file, {"schema_version": TRACE_STORE_SCHEMA_VERSION, "runs": runs})


__all__ = ["update_index"]

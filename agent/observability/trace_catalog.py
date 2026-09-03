"""Read-only run discovery and workspace-confined retention."""

from __future__ import annotations

import json
import logging
import shutil
import time
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agent.observability.liveness import TraceLivenessPolicy, TraceLivenessState
from agent.observability.trace_paths import (
    _OWNED_RUN_KEY_RE,
    TRACE_STORE_SCHEMA_VERSION,
    TraceCorruptError,
    TraceStoreError,
    TraceUnavailableError,
    _assert_no_link_descendants,
    _assert_owned_child,
    _assert_safe_path,
    _atomic_write,
    _resolve_trace_root,
    safe_run_key,
)
from agent.observability.trace_reader import MAX_TRACE_QUERY_LIMIT
from agent.observability.trace_runtime import TraceStore
from agent.observability.trace_types import TraceMetadata, TraceRetentionPolicy
from agent.runtime.path_safety import WorkspacePathError, assert_no_link_descendants

if TYPE_CHECKING:
    from agent.observability.trace_runtime import TraceStore


logger = logging.getLogger("LLM_Agent.observability")


def _retention_now(now: float | datetime | None) -> float:
    if isinstance(now, datetime):
        return now.timestamp()
    if isinstance(now, (int, float)) and not isinstance(now, bool):
        return float(now)
    return time.time()


def _load_retention_candidate(run_dir: Path) -> tuple[TraceMetadata, int]:
    metadata_file = run_dir / "metadata.json"
    try:
        metadata = TraceMetadata.from_dict(json.loads(metadata_file.read_text(encoding="utf-8")))
    except (OSError, UnicodeError, json.JSONDecodeError, TraceStoreError) as exc:
        raise TraceCorruptError(f"cannot inspect retained trace {run_dir.name}") from exc
    try:
        assert_no_link_descendants(run_dir)
        total = sum(item.stat().st_size for item in run_dir.rglob("*") if item.is_file())
    except (OSError, WorkspacePathError) as exc:
        raise TraceCorruptError(f"cannot inspect retained trace {run_dir.name}") from exc
    return metadata, total


def _retention_candidates(
    catalog: "TraceCatalog",
) -> list[tuple[TraceMetadata, Path, int]]:
    candidates: list[tuple[TraceMetadata, Path, int]] = []
    for run_dir in catalog._owned_run_dirs():
        metadata, total = _load_retention_candidate(run_dir)
        if not metadata.active:
            candidates.append((metadata, run_dir, total))
    candidates.sort(key=lambda item: (item[0].end_time or item[0].start_time, item[0].run_id))
    return candidates


def _candidate_age(metadata: TraceMetadata, current_time: float) -> float:
    try:
        return current_time - datetime.fromisoformat(metadata.end_time or metadata.start_time).timestamp()
    except ValueError:
        return 0


def _retention_removals(
    candidates: list[tuple[TraceMetadata, Path, int]],
    policy: TraceRetentionPolicy,
    current_time: float,
) -> list[tuple[TraceMetadata, Path, int]]:
    remove: list[tuple[TraceMetadata, Path, int]] = []
    remaining = list(candidates)
    for candidate in candidates:
        if _candidate_age(candidate[0], current_time) > policy.max_age_seconds:
            remove.append(candidate)
            remaining.remove(candidate)
    while len(remaining) > policy.max_runs:
        remove.append(remaining.pop(0))
    removed_keys = {(metadata.run_id, run_dir) for metadata, run_dir, _ in remove}
    total_size = sum(
        size
        for metadata, run_dir, size in candidates
        if (metadata.run_id, run_dir) not in removed_keys
    )
    for candidate in candidates:
        if total_size <= policy.max_bytes:
            break
        metadata, run_dir, size = candidate
        if (metadata.run_id, run_dir) in removed_keys:
            continue
        remove.append(candidate)
        removed_keys.add((metadata.run_id, run_dir))
        total_size -= size
    return remove


def _remove_retention_candidates(
    trace_root: Path,
    candidates: list[tuple[TraceMetadata, Path, int]],
) -> tuple[str, ...]:
    removed_ids: list[str] = []
    for metadata, run_dir, _ in candidates:
        _assert_owned_child(trace_root, run_dir)
        _assert_no_link_descendants(run_dir)
        shutil.rmtree(run_dir)
        removed_ids.append(metadata.run_id)
    return tuple(removed_ids)


class TraceCatalog:
    """Read-only workspace catalog used by presentation and CLI adapters."""

    def __init__(self, workspace_paths: Any) -> None:
        self.trace_root = _resolve_trace_root(workspace_paths, create=False)
        self.index_file = self.trace_root / "index.json"

    def _index(self) -> Mapping[str, Any]:
        if not self.index_file.exists():
            return {"runs": {}}
        _assert_safe_path(self.index_file, directory=False)
        try:
            value = json.loads(self.index_file.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise TraceCorruptError("trace index cannot be read") from exc
        if not isinstance(value, Mapping) or not isinstance(value.get("runs", {}), Mapping):
            raise TraceCorruptError("trace index is invalid")
        return value

    def list_runs(self, *, limit: int = MAX_TRACE_QUERY_LIMIT) -> tuple[TraceMetadata, ...]:
        bounded = max(0, min(int(limit), MAX_TRACE_QUERY_LIMIT))
        records: list[TraceMetadata] = []
        for run_key, summary in self._index().get("runs", {}).items():
            if not isinstance(run_key, str) or not isinstance(summary, Mapping):
                continue
            if not _OWNED_RUN_KEY_RE.fullmatch(run_key):
                raise TraceCorruptError("trace index contains an unsafe run key")
            run_dir = self.trace_root / run_key
            try:
                _assert_owned_child(self.trace_root, run_dir)
                metadata_file = run_dir / "metadata.json"
                _assert_safe_path(metadata_file, directory=False)
                records.append(TraceMetadata.from_dict(json.loads(metadata_file.read_text(encoding="utf-8"))))
            except FileNotFoundError:
                continue
            except (OSError, UnicodeError, json.JSONDecodeError, TraceStoreError) as exc:
                raise TraceCorruptError(f"trace metadata is invalid for {run_key}") from exc
        records.sort(key=lambda item: (item.start_time, item.run_id), reverse=True)
        return tuple(records[:bounded])

    def find(self, run_id: str) -> TraceMetadata:
        key = safe_run_key(run_id)
        for metadata in self.list_runs():
            if metadata.run_id == run_id and safe_run_key(metadata.run_id) == key:
                return metadata
        raise TraceUnavailableError("selected run is not retained")

    def latest(
        self,
        *,
        active_first: bool = True,
        now: float | datetime | str | None = None,
        liveness_policy: TraceLivenessPolicy | None = None,
    ) -> TraceMetadata:
        records = self.list_runs()
        if not records:
            raise TraceUnavailableError("no retained traces")
        if active_first:
            policy = liveness_policy or TraceLivenessPolicy()
            active = next(
                (
                    item
                    for item in records
                    if policy.evaluate(item, now).state is TraceLivenessState.LIVE
                ),
                None,
            )
            if active is not None:
                return active
        return records[0]

    def open(self, run_id: str, **kwargs: Any) -> TraceStore:
        metadata = self.find(run_id)
        kwargs.pop("read_only", None)
        kwargs.pop("auto_start", None)
        return TraceStore(
            self,
            metadata.run_id,
            root_task_id=metadata.root_task_id,
            mode=metadata.observability_mode,
            read_only=True,
            auto_start=False,
            **kwargs,
        )

    def _owned_run_dirs(self) -> list[Path]:
        result: list[Path] = []
        if not self.trace_root.exists():
            return result
        for child in self.trace_root.iterdir():
            if child.name == "index.json":
                continue
            _assert_safe_path(child)
            if not _OWNED_RUN_KEY_RE.fullmatch(child.name):
                continue
            if not child.is_dir():
                continue
            _assert_owned_child(self.trace_root, child)
            _assert_no_link_descendants(child)
            result.append(child)
        return result

    def apply_retention(
        self,
        policy: TraceRetentionPolicy | None = None,
        *,
        now: float | datetime | None = None,
    ) -> tuple[str, ...]:
        selected = policy or TraceRetentionPolicy()
        current_time = _retention_now(now)
        candidates = _retention_candidates(self)
        remove = _retention_removals(candidates, selected, current_time)
        removed_ids = _remove_retention_candidates(self.trace_root, remove)
        logger.info(
            "trace retention evaluated: candidates=%d removed=%d",
            len(candidates),
            len(removed_ids),
        )
        if removed_ids:
            index = self._index()
            runs = {
                key: value
                for key, value in index.get("runs", {}).items()
                if isinstance(value, Mapping) and value.get("run_id") not in removed_ids
            }
            _atomic_write(self.index_file, {"schema_version": TRACE_STORE_SCHEMA_VERSION, "runs": runs})
        return tuple(removed_ids)


__all__ = ["TraceCatalog"]

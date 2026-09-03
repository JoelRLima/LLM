"""TraceStore runtime facade composed from writer and reader owners."""

from __future__ import annotations

import json
import threading
from collections import deque
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable

from agent.observability.modes import ObservabilityMode
from agent.observability.redaction import REDACTION_POLICY_VERSION
from agent.observability.trace_lifecycle import TraceLifecycleMixin
from agent.observability.trace_paths import (
    TRACE_STORE_SCHEMA_VERSION,
    TraceClosedError,
    TraceCorruptError,
    TraceStoreError,
    TraceUnavailableError,
    _assert_owned_child,
    _assert_safe_path,
    _resolve_trace_root,
    safe_run_key,
)
from agent.observability.trace_reader import MAX_TRACE_QUERY_LIMIT, TraceReaderMixin
from agent.observability.trace_types import TraceCompleteness, TraceMetadata
from agent.observability.trace_writer import TraceWriterMixin

if TYPE_CHECKING:
    from agent.observability.trace_catalog import TraceCatalog

DEFAULT_QUEUE_CAPACITY = 128
DEFAULT_SHUTDOWN_TIMEOUT_SECONDS = 2.0


class TraceStore(TraceLifecycleMixin, TraceWriterMixin, TraceReaderMixin):
    """One application-owned per-run trace writer and read adapter."""

    def __init__(
        self,
        workspace_paths: Any,
        run_id: str,
        *,
        root_task_id: str | None = None,
        mode: ObservabilityMode | str = ObservabilityMode.NORMAL,
        observability_mode: ObservabilityMode | str | None = None,
        queue_capacity: int = DEFAULT_QUEUE_CAPACITY,
        clock: Callable[[], Any] | None = None,
        shutdown_timeout_seconds: float = DEFAULT_SHUTDOWN_TIMEOUT_SECONDS,
        auto_start: bool = True,
        read_only: bool = False,
    ) -> None:
        if isinstance(queue_capacity, bool) or not isinstance(queue_capacity, int) or queue_capacity < 1:
            raise ValueError("queue_capacity must be a positive integer")
        if shutdown_timeout_seconds < 0:
            raise ValueError("shutdown_timeout_seconds must be non-negative")
        self._read_only = bool(read_only)
        self._clock = clock
        self.trace_root = _resolve_trace_root(workspace_paths, create=not self._read_only)
        self.run_id = run_id
        self.root_task_id = root_task_id or run_id
        if not isinstance(self.root_task_id, str) or not self.root_task_id.strip():
            raise ValueError("root_task_id must be a non-empty string")
        self.mode = ObservabilityMode.parse(observability_mode if observability_mode is not None else mode)
        self.queue_capacity = queue_capacity
        self._shutdown_timeout_seconds = float(shutdown_timeout_seconds)
        self.run_key = safe_run_key(run_id)
        self.run_dir = self.trace_root / self.run_key
        self.trace_file = self.run_dir / "trace.jsonl"
        self.metadata_file = self.run_dir / "metadata.json"
        self._ensure_owned_run_paths()
        existing: TraceMetadata | None = self._read_metadata_file() if self.metadata_file.exists() else None
        if existing is not None:
            if existing.run_id != self.run_id:
                raise TraceStoreError("trace run-key collision or mismatched metadata")
            if not existing.active and not self._read_only:
                raise TraceClosedError("selected trace is already closed")
            self._metadata_values = existing.to_dict()
            self.root_task_id = existing.root_task_id
            self.mode = existing.observability_mode
        else:
            now = self._now_text()
            self._metadata_values = TraceMetadata(
                schema_version=TRACE_STORE_SCHEMA_VERSION,
                run_id=self.run_id,
                root_task_id=self.root_task_id,
                start_time=now,
                end_time=None,
                observability_mode=self.mode,
                status=TraceCompleteness.ACTIVE.value,
                highest_sequence_accepted=0,
                highest_sequence_persisted=0,
                semantic_count=0,
                diagnostic_count=0,
                gap_count=0,
                dropped_count=0,
                suppressed_count=0,
                dropped_by_reason={},
                redaction_policy_version=REDACTION_POLICY_VERSION,
                completeness=TraceCompleteness.ACTIVE,
                final_outcome=None,
                active=True,
            ).to_dict()
        self._next_sequence = int(self._metadata_values["highest_sequence_accepted"]) + 1
        self._pending: deque[Any] = deque()
        self._pending_gaps: list[tuple[int, int, str, int]] = []
        self._metadata_dirty = False
        self._metadata_revision = 0
        self._metadata_publish_lock = threading.RLock()
        self._condition = threading.Condition(threading.RLock())
        self._inflight = False
        self._publication_in_flight = 0
        self._closing = False
        self._finalization_pending = False
        self._finalization_timed_out = False
        self._closed = self._read_only
        self._worker_started = False
        self._writer_error: str | None = None
        self._file_handle: Any = None
        self._worker = threading.Thread(
            target=self._writer_loop,
            name=f"llm-agent-trace-{self.run_key[:12]}",
            daemon=True,
        )
        if not self._read_only:
            self._write_metadata()
            self._update_index()
            if auto_start:
                self.start()

    def _now_text(self) -> str:
        if self._clock is None:
            return datetime.now(timezone.utc).isoformat()
        value = self._clock()
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()
        raise TypeError("trace clock must return datetime or a number")

    @classmethod
    def open(cls, workspace_paths: Any, run_id: str, **kwargs: Any) -> "TraceStore":
        kwargs["read_only"] = True
        kwargs["auto_start"] = False
        return cls(workspace_paths, run_id, **kwargs)

    @classmethod
    def for_workspace(cls, workspace_paths: Any) -> "TraceCatalog":
        from agent.observability.trace_catalog import TraceCatalog

        return TraceCatalog(workspace_paths)

    @classmethod
    def list_runs(cls, workspace_paths: Any, *, limit: int = MAX_TRACE_QUERY_LIMIT) -> tuple[TraceMetadata, ...]:
        return cls.for_workspace(workspace_paths).list_runs(limit=limit)

    def _ensure_owned_run_paths(self) -> None:
        _assert_safe_path(self.trace_root, directory=True)
        if not self.run_dir.exists():
            if self._read_only:
                raise TraceUnavailableError("selected trace is not retained")
            self.run_dir.mkdir(parents=True, exist_ok=True)
        _assert_owned_child(self.trace_root, self.run_dir)
        _assert_safe_path(self.run_dir, directory=True)
        _assert_safe_path(self.trace_file)
        _assert_safe_path(self.metadata_file)

    def _read_metadata_file(self) -> TraceMetadata:
        _assert_safe_path(self.metadata_file, directory=False)
        try:
            raw = json.loads(self.metadata_file.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise TraceCorruptError("trace metadata cannot be read") from exc
        return TraceMetadata.from_dict(raw)

    def _metadata_snapshot(self) -> TraceMetadata:
        return TraceMetadata.from_dict(dict(self._metadata_values))

    @property
    def metadata(self) -> TraceMetadata:
        with self._condition:
            return self._metadata_snapshot()

    @property
    def completeness(self) -> TraceCompleteness:
        return self.metadata.completeness


TraceStoreReader = TraceStore


__all__ = [
    "DEFAULT_QUEUE_CAPACITY",
    "DEFAULT_SHUTDOWN_TIMEOUT_SECONDS",
    "MAX_TRACE_QUERY_LIMIT",
    "TraceStore",
    "TraceStoreReader",
]

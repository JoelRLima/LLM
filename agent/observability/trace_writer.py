"""Bounded asynchronous writer mixin for one trace run."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, cast

from agent.observability.diagnostics import DiagnosticRecord
from agent.observability.envelopes import GapMarker, ObservationEnvelope, ObservationSource
from agent.observability.trace_index import update_index
from agent.observability.trace_paths import (
    TraceClosedError,
    TraceStoreError,
    _assert_safe_path,
    _atomic_write,
)
from agent.observability.trace_types import TraceCompleteness
from agent.observability.trace_writer_publication import (
    publish_metadata,
    publish_metadata_bounded,
)
from agent.runtime.events import RuntimeEvent

logger = logging.getLogger("LLM_Agent.observability")


@dataclass(slots=True)
class _QueuedEnvelope:
    envelope: ObservationEnvelope
    source: ObservationSource


class TraceWriterMixin:
    """Writer lifecycle kept separate from the public TraceStore facade."""

    _writer_error: str | None
    _metadata_dirty: bool

    def _mark_metadata_dirty_locked(self: Any) -> None:
        """Record a metadata generation while the ingestion condition is held."""

        self._metadata_revision += 1
        self._metadata_dirty = True
        self._heartbeat_quiesced = False
        self._heartbeat_recovery_pending = False

    def start(self: Any) -> None:
        """Start the bounded writer when construction was deferred for a test/owner."""

        if self._read_only or self._closed:
            return
        if not self._worker_started:
            self._worker.start()
            self._worker_started = True

    def _write_metadata(self: Any, metadata: dict[str, Any] | None = None) -> None:
        if metadata is None:
            with self._condition:
                metadata = self._metadata_snapshot().to_dict()
        _atomic_write(self.metadata_file, metadata)

    def _index_file(self: Any) -> Any:
        return self.trace_root / "index.json"

    def _update_index(self: Any, metadata: dict[str, Any] | None = None) -> None:
        if metadata is None:
            with self._condition:
                metadata = self._metadata_snapshot().to_dict()
        update_index(
            index_file=self._index_file(),
            run_key=self.run_key,
            run_id=self.run_id,
            root_task_id=self.root_task_id,
            metadata=metadata,
            atomic_write=_atomic_write,
        )

    def _publish_metadata(
        self: Any,
        *,
        force: bool = False,
        allow_latched: bool = False,
        final: bool = False,
    ) -> None:
        publish_metadata(
            self,
            force=force,
            allow_latched=allow_latched,
            final=final,
        )

    def _publish_metadata_bounded(self: Any, timeout_seconds: float) -> bool:
        return publish_metadata_bounded(self, timeout_seconds)

    def _record_drop_locked(self: Any, sequence: int, reason: str, *, enqueue_gap: bool = True) -> None:
        dropped_count = int(self._metadata_values.get("dropped_count", 0)) + 1
        reasons = dict(self._metadata_values.get("dropped_by_reason", {}))
        reasons[reason] = int(reasons.get(reason, 0)) + 1
        self._metadata_values["dropped_count"] = dropped_count
        self._metadata_values["dropped_by_reason"] = reasons
        self._metadata_values["status"] = TraceCompleteness.PARTIAL.value
        self._metadata_values["completeness"] = TraceCompleteness.PARTIAL.value
        if enqueue_gap and self._writer_error is None:
            self._pending_gaps.append((sequence, sequence, reason, 1))
        self._mark_metadata_dirty_locked()

    def _make_envelope_locked(
        self: Any,
        value: RuntimeEvent | DiagnosticRecord,
        source: ObservationSource,
    ) -> ObservationEnvelope:
        sequence = self._next_sequence
        self._next_sequence += 1
        self._metadata_values["highest_sequence_accepted"] = sequence
        self._mark_metadata_dirty_locked()
        if source is ObservationSource.RUNTIME_EVENT:
            if not isinstance(value, RuntimeEvent):
                raise TypeError("semantic trace observations must be RuntimeEvent instances")
            return ObservationEnvelope.runtime_event(value, sequence)
        if source is ObservationSource.DIAGNOSTIC:
            if not isinstance(value, DiagnosticRecord):
                raise TypeError("diagnostic trace observations must be DiagnosticRecord instances")
            return ObservationEnvelope.diagnostic(value, sequence, run_id=self.run_id)
        raise TypeError("unsupported trace observation source")

    def _materialize_gap_locked(self: Any) -> None:
        if self._writer_error is not None or not self._pending_gaps or len(self._pending) >= self.queue_capacity:
            return
        start, end, reason, count = self._pending_gaps.pop(0)
        marker = GapMarker(
            reason=reason,
            start_sequence=start,
            end_sequence=end,
            dropped_count=count,
            timestamp=self._now_text(),
        )
        sequence = self._next_sequence
        self._next_sequence += 1
        self._metadata_values["highest_sequence_accepted"] = sequence
        self._mark_metadata_dirty_locked()
        self._pending.append(_QueuedEnvelope(ObservationEnvelope.gap(self.run_id, sequence, marker), ObservationSource.GAP))

    def _queue_locked(self: Any, envelope: ObservationEnvelope) -> bool:
        self._materialize_gap_locked()
        if len(self._pending) >= self.queue_capacity:
            if envelope.source is ObservationSource.RUNTIME_EVENT:
                diagnostic_index = next(
                    (index for index, item in enumerate(self._pending) if item.source is ObservationSource.DIAGNOSTIC),
                    None,
                )
                if diagnostic_index is not None:
                    shed = self._pending[diagnostic_index]
                    del self._pending[diagnostic_index]
                    self._record_drop_locked(shed.envelope.sequence, "diagnostic_shed_for_semantic")
                else:
                    return False
            else:
                return False
        self._pending.append(_QueuedEnvelope(envelope, envelope.source))
        return True

    def _accept(self: Any, value: RuntimeEvent | DiagnosticRecord, source: ObservationSource) -> int | None:
        with self._condition:
            if self._read_only or self._closed or self._closing:
                raise TraceClosedError("trace store is closing or closed")
            if (
                source is ObservationSource.DIAGNOSTIC
                and isinstance(value, DiagnosticRecord)
                and not value.is_allowed_in(self.mode)
            ):
                self._metadata_values["suppressed_count"] = int(self._metadata_values.get("suppressed_count", 0)) + 1
                suppressed = dict(self._metadata_values.get("dropped_by_reason", {}))
                suppressed["mode_suppressed"] = int(suppressed.get("mode_suppressed", 0)) + 1
                self._metadata_values["dropped_by_reason"] = suppressed
                self._mark_metadata_dirty_locked()
                return None
            envelope = cast(ObservationEnvelope, self._make_envelope_locked(value, source))
            if self._writer_error is not None:
                self._record_drop_locked(envelope.sequence, "writer_failure", enqueue_gap=False)
                self._condition.notify_all()
                return cast(int | None, envelope.sequence)
            if self._queue_locked(envelope):
                if source is ObservationSource.RUNTIME_EVENT:
                    self._metadata_values["semantic_count"] = int(self._metadata_values["semantic_count"]) + 1
                    self._metadata_values["last_semantic_activity"] = envelope.timestamp
                else:
                    self._metadata_values["diagnostic_count"] = int(self._metadata_values["diagnostic_count"]) + 1
                self._mark_metadata_dirty_locked()
                self._condition.notify_all()
                return cast(int | None, envelope.sequence)
            reason = "diagnostic_queue_pressure" if source is ObservationSource.DIAGNOSTIC else "semantic_queue_pressure"
            self._record_drop_locked(envelope.sequence, reason)
            self._condition.notify_all()
            return cast(int | None, envelope.sequence)

    def append(self: Any, event: RuntimeEvent) -> int | None:
        """Accept one semantic event without synchronous disk I/O."""

        return cast(int | None, self._accept(event, ObservationSource.RUNTIME_EVENT))

    def append_event(self: Any, event: RuntimeEvent) -> int | None:
        return cast(int | None, self.append(event))

    def emit(self: Any, event: RuntimeEvent) -> None:
        """RuntimeEvent sink interface; infrastructure errors are isolated."""

        try:
            self.append(event)
        except Exception as exc:
            self._note_writer_failure(exc)

    def append_diagnostic(self: Any, record: DiagnosticRecord) -> int | None:
        return cast(int | None, self._accept(record, ObservationSource.DIAGNOSTIC))

    def emit_diagnostic(self: Any, record: DiagnosticRecord) -> None:
        try:
            self.append_diagnostic(record)
        except Exception as exc:
            self._note_writer_failure(exc)

    record_diagnostic = emit_diagnostic

    def _note_writer_failure(self: Any, exc: BaseException, *, failed_sequence: int | None = None) -> None:
        with self._condition:
            first_failure = self._writer_error is None
            if first_failure:
                self._writer_error = type(exc).__name__
                if failed_sequence is not None:
                    self._record_drop_locked(failed_sequence, "writer_failure", enqueue_gap=False)
                while self._pending:
                    pending = self._pending.popleft()
                    self._record_drop_locked(pending.envelope.sequence, "writer_failure", enqueue_gap=False)
                self._pending_gaps.clear()
            self._metadata_values["status"] = TraceCompleteness.UNCLEAN.value
            self._metadata_values["completeness"] = TraceCompleteness.UNCLEAN.value
            self._mark_metadata_dirty_locked()
            self._condition.notify_all()
        if first_failure:
            logger.warning("trace observer failure: %s", type(exc).__name__)

    def _open_writer_file(self: Any) -> Any:
        _assert_safe_path(self.run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        _assert_safe_path(self.run_dir, directory=True)
        _assert_safe_path(self.trace_file)
        return self.trace_file.open("a", encoding="utf-8", newline="\n")

    def _persist_envelope(self: Any, item: _QueuedEnvelope) -> None:
        with self._condition:
            if self._writer_error is not None:
                raise TraceStoreError("trace writer failure is latched")
        if self._file_handle is None:
            self._file_handle = self._open_writer_file()
        self._file_handle.write(item.envelope.to_json() + "\n")
        self._file_handle.flush()
        with self._condition:
            self._metadata_values["highest_sequence_persisted"] = max(
                int(self._metadata_values["highest_sequence_persisted"]), item.envelope.sequence
            )
            if item.source is ObservationSource.GAP:
                self._metadata_values["gap_count"] = int(self._metadata_values["gap_count"]) + 1
            self._mark_metadata_dirty_locked()

    def _persist_dirty_metadata(self: Any) -> None:
        """Publish loss state only from the writer/control path."""

        with self._condition:
            final = bool(self._closed and not self._metadata_values.get("active", True))
        self._publish_metadata(final=final)

    def _writer_loop(self: Any) -> None:
        from agent.observability.trace_writer_worker import run_writer

        run_writer(self)

    def flush(self: Any, timeout_seconds: float | None = None) -> bool:
        timeout = self._shutdown_timeout_seconds if timeout_seconds is None else max(0.0, timeout_seconds)
        deadline = time.monotonic() + timeout
        with self._condition:
            while (
                self._pending
                or self._pending_gaps
                or self._inflight
                or (self._metadata_dirty and not getattr(self, "_heartbeat_quiesced", False))
                or self._publication_in_flight > 0
            ):
                if self._writer_error is not None:
                    return False
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.notify_all()
                self._condition.wait(timeout=remaining)
            return self._writer_error is None

__all__ = ["TraceWriterMixin"]

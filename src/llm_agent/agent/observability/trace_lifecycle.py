"""Final-outcome and bounded-close lifecycle mixin for traces."""

from __future__ import annotations

import time
from collections.abc import Mapping
from datetime import datetime
from typing import Any, cast

from llm_agent.agent.observability.redaction import redact_observation_value
from llm_agent.agent.observability.trace_paths import TraceClosedError, _time_text
from llm_agent.agent.observability.trace_types import TraceCompleteness, TraceMetadata


class TraceLifecycleMixin:
    _metadata_dirty_since: float | None
    """Lifecycle methods kept separate from queue and parser implementation."""

    @property
    def finalization_settled(self: Any) -> bool:
        """Return whether this closed run is safe to include in retention."""

        with self._condition:
            return bool(
                self._closed
                and not self._finalization_pending
                and not self._finalization_timed_out
                and self._publication_in_flight == 0
                and not self._worker.is_alive()
                and not self._inflight
                and not self._metadata_dirty
                and self._writer_error is None
                and not self._metadata_values.get("active", True)
                and self._metadata_values.get("completeness")
                in {TraceCompleteness.COMPLETE.value, TraceCompleteness.PARTIAL.value}
            )

    def set_final_outcome(
        self: Any,
        outcome: Mapping[str, Any] | Any,
        *,
        timeout_seconds: float | None = None,
    ) -> None:
        """Capture the final outcome without performing caller-side I/O.

        Durable finalization is owned by ``close`` and the existing writer
        control path.  Keeping this method mutation-only prevents a result
        path from waiting on a stuck metadata/index publisher.
        """

        del timeout_seconds
        if isinstance(outcome, Mapping):
            safe_value: Mapping[str, Any] = outcome
        else:
            to_dict = getattr(outcome, "to_dict", None)
            projected = to_dict() if callable(to_dict) else None
            safe_value = projected if isinstance(projected, Mapping) else {"summary": "<unavailable>"}
        safe = redact_observation_value(safe_value)
        if not isinstance(safe, Mapping):
            safe = {"summary": "<unavailable>"}
        with self._condition:
            if self._read_only or self._closed or self._closing:
                raise TraceClosedError("trace store is read-only or closed")
            self._metadata_values["final_outcome"] = dict(safe)
            self._mark_metadata_dirty_locked()
            self._condition.notify_all()

    def heartbeat(self: Any, timestamp: str | datetime | None = None) -> None:
        with self._condition:
            if self._read_only or self._closed or self._closing:
                raise TraceClosedError("trace store is read-only or closed")
            self._metadata_values["last_observer_heartbeat"] = self._now_text() if timestamp is None else _time_text(timestamp)
            if not self._metadata_dirty:
                self._heartbeat_quiesced = False
                if self._publication_in_flight == 0:
                    self._mark_metadata_dirty_locked()
                else:
                    # The acknowledgement may have observed a clean snapshot
                    # while its publication still owns the in-flight marker.
                    # Keep this coalesced heartbeat pending without creating a
                    # new independent revision.
                    self._metadata_dirty = True
                    if self._metadata_dirty_since is None:
                        self._metadata_dirty_since = time.monotonic()
            elif getattr(self, "_heartbeat_quiesced", False) and self._publication_in_flight == 0:
                # Re-arm one coalesced writer opportunity after recovery.
                self._heartbeat_quiesced = False
            self._condition.notify_all()

    def _closed_snapshot(self: Any) -> TraceMetadata:
        with self._condition:
            metadata = cast(TraceMetadata, self._metadata_snapshot())
            release_writer_lock = (
                not self._worker.is_alive()
                and not self._inflight
                and self._publication_in_flight == 0
            )
            if release_writer_lock:
                self._release_writer_lock()
            return metadata

    def _record_initial_close_state(self: Any, clean_drain: bool) -> None:
        with self._condition:
            if not clean_drain or self._writer_error is not None:
                completeness = TraceCompleteness.UNCLEAN
            elif int(self._metadata_values.get("dropped_count", 0)) > 0:
                completeness = TraceCompleteness.PARTIAL
            else:
                completeness = TraceCompleteness.COMPLETE
            self._metadata_values["completeness"] = completeness.value
            self._metadata_values["status"] = completeness.value
            self._metadata_values["end_time"] = self._now_text()
            self._metadata_values["active"] = False
            self._closed = True
            self._finalization_pending = False
            if completeness is TraceCompleteness.UNCLEAN:
                self._finalization_timed_out = True
            self._mark_metadata_dirty_locked()
            self._condition.notify_all()

    def _record_final_drain_state(self: Any, final_drain: bool) -> None:
        with self._condition:
            if not final_drain or self._worker.is_alive() or self._writer_error is not None:
                self._finalization_timed_out = True
                self._metadata_values["completeness"] = TraceCompleteness.UNCLEAN.value
                self._metadata_values["status"] = TraceCompleteness.UNCLEAN.value
                self._mark_metadata_dirty_locked()
            self._condition.notify_all()

    def _publish_final_fallback(self: Any, deadline: float) -> None:
        # After the writer stops, this bounded attempt is the only remaining
        # path that can publish the unclean terminal snapshot.
        with self._condition:
            fallback_needed = not self._worker.is_alive() and self._metadata_dirty
        if not fallback_needed:
            return
        published = self._publish_metadata_bounded(max(0.0, deadline - time.monotonic()))
        if published:
            return
        with self._condition:
            self._finalization_timed_out = True
            self._metadata_values["completeness"] = TraceCompleteness.UNCLEAN.value
            self._metadata_values["status"] = TraceCompleteness.UNCLEAN.value
            self._mark_metadata_dirty_locked()
            self._condition.notify_all()

    def close(self: Any, *, timeout_seconds: float | None = None) -> TraceMetadata:
        timeout = self._shutdown_timeout_seconds if timeout_seconds is None else max(0.0, timeout_seconds)
        deadline = time.monotonic() + timeout
        initial_drain_budget = max(0.0, timeout - min(0.5, timeout / 4.0))
        if not self._worker_started:
            self.start()
        with self._condition:
            if self._read_only or self._closed:
                return cast(TraceMetadata, self._closed_snapshot())
            self._closing = True
            self._finalization_pending = True
            self._condition.notify_all()

        clean_drain = self.flush(initial_drain_budget)
        self._record_initial_close_state(clean_drain)
        final_drain = self.flush(max(0.0, deadline - time.monotonic()))
        if self._worker.is_alive():
            self._worker.join(timeout=max(0.0, deadline - time.monotonic()))
        self._record_final_drain_state(final_drain)
        self._publish_final_fallback(deadline)
        with self._condition:
            metadata = cast(TraceMetadata, self._metadata_snapshot())
            release_writer_lock = (
                not self._worker.is_alive()
                and not self._inflight
                and self._publication_in_flight == 0
            )
        if release_writer_lock:
            self._release_writer_lock()
        return metadata


__all__ = ["TraceLifecycleMixin"]

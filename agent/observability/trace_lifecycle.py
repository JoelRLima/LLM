"""Final-outcome and bounded-close lifecycle mixin for traces."""

from __future__ import annotations

import time
from collections.abc import Mapping
from datetime import datetime
from typing import Any, cast

from agent.observability.redaction import redact_observation_value
from agent.observability.trace_paths import TraceClosedError, _time_text
from agent.observability.trace_types import TraceCompleteness, TraceMetadata


class TraceLifecycleMixin:
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
            self._mark_metadata_dirty_locked()
        try:
            self._publish_metadata(force=True)
        except Exception as exc:
            self._note_writer_failure(exc)
            raise

    def close(self: Any, *, timeout_seconds: float | None = None) -> TraceMetadata:
        timeout = self._shutdown_timeout_seconds if timeout_seconds is None else max(0.0, timeout_seconds)
        deadline = time.monotonic() + timeout
        if not self._worker_started:
            self.start()
        with self._condition:
            if self._read_only or self._closed:
                return cast(TraceMetadata, self._metadata_snapshot())
            self._closing = True
            self._finalization_pending = True
            self._condition.notify_all()

        clean_drain = self.flush(max(0.0, deadline - time.monotonic()))
        with self._condition:
            # The worker is intentionally kept alive by ``_finalization_pending``
            # until the final snapshot below has been handed to its control
            # path, so liveness here is not itself an unclean result.
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

        final_drain = self.flush(max(0.0, deadline - time.monotonic()))
        if self._worker.is_alive():
            self._worker.join(timeout=max(0.0, deadline - time.monotonic()))
        with self._condition:
            if not final_drain or self._worker.is_alive() or self._writer_error is not None:
                self._finalization_timed_out = True
                self._metadata_values["completeness"] = TraceCompleteness.UNCLEAN.value
                self._metadata_values["status"] = TraceCompleteness.UNCLEAN.value
                self._mark_metadata_dirty_locked()
            self._condition.notify_all()

        # A failed writer has already terminated, so there is no control path
        # left to publish the unclean terminal snapshot.  Use one bounded,
        # daemonized final attempt in that exceptional case; it can never
        # extend this caller's deadline or be force-killed.
        with self._condition:
            fallback_needed = not self._worker.is_alive() and self._metadata_dirty
        if fallback_needed:
            published = self._publish_metadata_bounded(max(0.0, deadline - time.monotonic()))
            if not published:
                with self._condition:
                    self._finalization_timed_out = True
                    self._metadata_values["completeness"] = TraceCompleteness.UNCLEAN.value
                    self._metadata_values["status"] = TraceCompleteness.UNCLEAN.value
                    self._mark_metadata_dirty_locked()
                    self._condition.notify_all()
        with self._condition:
            return cast(TraceMetadata, self._metadata_snapshot())


__all__ = ["TraceLifecycleMixin"]

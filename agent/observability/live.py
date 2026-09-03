"""Application-owned live observation sessions and read-only attachments."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Mapping

from agent.observability.diagnostics import DiagnosticRecord
from agent.observability.silence import SilenceLevel, SilencePolicy, SilenceStatus, clock_now
from agent.observability.trace_store import TraceMetadata, TraceStore
from agent.observability.trace_types import TraceRetentionPolicy
from agent.runtime.events import RuntimeEvent

logger = logging.getLogger("LLM_Agent.observability")


class ObservationAttachment:
    """Read-only live/history handle; it owns no execution lock or task state."""

    def __init__(self, workspace_paths: Any, run_id: str) -> None:
        self._store = TraceStore.open(workspace_paths, run_id)
        self.run_id = run_id
        self._detached = False

    @property
    def detached(self) -> bool:
        return self._detached

    @property
    def metadata(self) -> TraceMetadata:
        return self._store.metadata

    def tail(self, *, after_sequence: int = 0, limit: int = 256) -> tuple[Any, ...]:
        if self._detached:
            return ()
        return self._store.tail(after_sequence=after_sequence, limit=limit)

    def read_result(self) -> Any:
        if self._detached:
            return self._store.read_result()
        return self._store.read_result()

    def detach(self) -> None:
        self._detached = True

    close = detach


class ObservationSession:
    """One app-owned trace sink for one active run."""

    def __init__(
        self,
        workspace_paths: Any,
        run_id: str,
        *,
        root_task_id: str | None = None,
        mode: Any = "normal",
        clock: Callable[[], Any] | None = None,
        silence_policy: SilencePolicy | None = None,
        heartbeat_interval_seconds: float = 1.0,
        trace_store: TraceStore | None = None,
        retention_policy: TraceRetentionPolicy | None = None,
    ) -> None:
        if isinstance(heartbeat_interval_seconds, bool) or not isinstance(heartbeat_interval_seconds, (int, float)):
            raise TypeError("heartbeat interval must be numeric")
        if heartbeat_interval_seconds <= 0:
            raise ValueError("heartbeat interval must be positive")
        self.workspace_paths = workspace_paths
        self.run_id = run_id
        self.clock = clock
        self.silence_policy = silence_policy or SilencePolicy()
        self.heartbeat_interval_seconds = float(heartbeat_interval_seconds)
        self.retention_policy = retention_policy
        self.store = trace_store or TraceStore(
            workspace_paths,
            run_id,
            root_task_id=root_task_id,
            mode=mode,
            clock=clock,
        )
        self._dispatcher: Any = None
        self._stop = threading.Event()
        self._heartbeat_thread: threading.Thread | None = None
        self._closed = False
        self._last_watchdog: str | None = None
        self._active_context: dict[str, Any] = {}

    @classmethod
    def for_correlation(
        cls,
        workspace_paths: Any,
        correlation: Any,
        *,
        mode: Any = "normal",
        **kwargs: Any,
    ) -> "ObservationSession":
        return cls(
            workspace_paths,
            correlation.run_id,
            root_task_id=correlation.root_task_id,
            mode=mode,
            **kwargs,
        )

    @property
    def metadata(self) -> TraceMetadata:
        return self.store.metadata

    @property
    def closed(self) -> bool:
        return self._closed

    def start(self, dispatcher: Any | None = None) -> "ObservationSession":
        if self._closed:
            return self
        if dispatcher is not None:
            self._dispatcher = dispatcher
            add_sink = getattr(dispatcher, "add_sink", None)
            if callable(add_sink):
                add_sink(self)
        self.store.start()
        if self._heartbeat_thread is None:
            self._heartbeat_thread = threading.Thread(
                target=self._heartbeat_loop,
                name=f"llm-agent-observer-{self.run_id[:12]}",
                daemon=True,
            )
            self._heartbeat_thread.start()
        return self

    def _heartbeat_loop(self) -> None:
        while not self._stop.wait(self.heartbeat_interval_seconds):
            try:
                self.store.heartbeat()
            except Exception:
                # TraceStore already records observer failures without allowing
                # this maintenance thread to touch task semantics.
                continue

    def emit(self, event: RuntimeEvent) -> None:
        """RuntimeEvent sink boundary; no caller performs trace I/O directly."""

        if event.kind.value == "watchdog":
            self._last_watchdog = event.timestamp
        self._active_context = {
            key: value
            for key, value in (
                ("task_id", event.task_id),
                ("plan_id", event.plan_id),
                ("step_id", event.step_id),
                ("invocation_id", event.invocation_id),
            )
            if value is not None
        }
        self.store.emit(event)

    def emit_diagnostic(self, record: DiagnosticRecord) -> None:
        self.store.emit_diagnostic(record)

    record_diagnostic = emit_diagnostic

    def silence_status(self, metadata: TraceMetadata | None = None) -> SilenceStatus:
        selected = metadata or self.metadata
        return self.silence_policy.evaluate(
            last_semantic_activity=selected.last_semantic_activity,
            last_observer_heartbeat=selected.last_observer_heartbeat,
            now=clock_now(self.clock),
            canonical_watchdog=self._last_watchdog,
            active_context=self._active_context,
        )

    def attach(self) -> ObservationAttachment:
        return ObservationAttachment(self.workspace_paths, self.run_id)

    def finish(self, outcome: Mapping[str, Any] | Any = None, *, timeout_seconds: float | None = None) -> TraceMetadata:
        if self._closed:
            return self.metadata
        timeout = (
            self.store._shutdown_timeout_seconds
            if timeout_seconds is None
            else max(0.0, timeout_seconds)
        )
        deadline = time.monotonic() + timeout
        if outcome is not None:
            try:
                self.store.set_final_outcome(outcome, timeout_seconds=max(0.0, deadline - time.monotonic()))
            except Exception:
                pass
        self._stop.set()
        heartbeat_thread = self._heartbeat_thread
        if heartbeat_thread is not None and heartbeat_thread.is_alive():
            heartbeat_thread.join(timeout=max(0.0, deadline - time.monotonic()))
        if self._dispatcher is not None:
            remove_sink = getattr(self._dispatcher, "remove_sink", None)
            if callable(remove_sink):
                remove_sink(self)
        metadata = self.store.close(timeout_seconds=max(0.0, deadline - time.monotonic()))
        self._closed = True
        if self.store.finalization_settled:
            self._apply_retention()
        return metadata

    def _apply_retention(self) -> None:
        """Run bounded cleanup after trace finalization, outside ingestion."""

        try:
            from agent.observability.trace_catalog import TraceCatalog

            removed = TraceCatalog(self.workspace_paths).apply_retention(
                self.retention_policy,
                now=clock_now(self.clock),
            )
            if removed:
                logger.info("trace retention removed %d finalized run(s)", len(removed))
        except Exception as exc:
            logger.warning("trace retention failure: %s", type(exc).__name__)

    close = finish


__all__ = [
    "ObservationAttachment",
    "ObservationSession",
    "SilenceLevel",
    "SilencePolicy",
    "SilenceStatus",
]

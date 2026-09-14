"""Single interactive execution controller."""

from __future__ import annotations

import threading
from enum import Enum
from queue import Empty, Queue
from typing import Any, Callable

from agent.interfaces.cli.controller_models import (
    MAX_INPUT_CHARS,
    MAX_PENDING_CHARS,
    MAX_PENDING_ITEMS,
    PendingCapacityError,
    PendingItem,
    PendingStore,
    SubmissionEnvelope,
    SubmissionOutcome,
    WorkerMessage,
)
from agent.interfaces.cli.worker_stream import BoundedWorkerStream, WorkerStreamChunk


class ControllerState(str, Enum):
    IDLE = "IDLE"
    RUNNING = "RUNNING"
    WAITING_ATTENTION = "WAITING_ATTENTION"
    CANCELLING = "CANCELLING"
    SETTLING = "SETTLING"
    TERMINAL = "TERMINAL"


Executor = Callable[[SubmissionEnvelope, threading.Event, Callable[[Callable[[], None]], None]], Any]
WORKER_SETTLEMENT_TIMEOUT_SECONDS = 2.0


class WorkerSettlementTimeout(RuntimeError):
    """The non-daemon interactive worker did not settle within the bound."""


class InteractiveExecutionController:
    """Owns one non-daemon worker and its generation-tagged result channel."""

    def __init__(self, *, pending: PendingStore | None = None) -> None:
        self.pending = pending or PendingStore()
        self._lock = threading.RLock()
        self._state = ControllerState.IDLE
        self._next_generation = 1
        self._active_generation: int | None = None
        self._worker: threading.Thread | None = None
        self._cancel_event: threading.Event | None = None
        self._cancel_handles: list[Callable[[], None]] = []
        self._result_channel: Queue[WorkerMessage] = Queue(maxsize=4)
        self._stream_channel = BoundedWorkerStream()
        self._last_stream_generation: int | None = None
        self._stale_messages = 0
        self._closed = False

    @property
    def state(self) -> ControllerState:
        with self._lock:
            return self._state

    @property
    def current_generation(self) -> int | None:
        with self._lock:
            return self._active_generation

    @property
    def stale_message_count(self) -> int:
        with self._lock:
            return self._stale_messages

    @property
    def stream_channel(self) -> BoundedWorkerStream:
        return self._stream_channel

    def poll_stream(self) -> tuple[WorkerStreamChunk, ...]:
        """Drain the current/last generation on the prompt/UI thread."""

        with self._lock:
            generation = self._active_generation or self._last_stream_generation
        chunks = self._stream_channel.poll(generation)
        if generation is not None and not chunks and self._stream_channel.pending is False:
            with self._lock:
                if self._active_generation is None and self._last_stream_generation == generation:
                    self._last_stream_generation = None
        return chunks

    def is_busy(self) -> bool:
        return self.state is not ControllerState.IDLE

    def _start_locked(self, envelope: SubmissionEnvelope, execute: Executor) -> SubmissionOutcome:
        if self._closed:
            return SubmissionOutcome("REJECTED_PRESERVE", reason="CONTROLLER_CLOSED")
        if self._state is not ControllerState.IDLE or self._worker is not None:
            raise RuntimeError("second interactive worker is forbidden")
        generation = self._next_generation
        self._next_generation += 1
        active = SubmissionEnvelope(
            generation,
            envelope.visible_text,
            envelope.command_id,
            envelope.routing_kind,
            envelope.busy_policy,
            envelope.busy_submit,
            envelope.payload,
            envelope.boundary,
            envelope.owner,
        )
        cancel_event = threading.Event()
        self._active_generation = generation
        self._last_stream_generation = None
        self._stream_channel.begin_generation(generation)
        self._cancel_event = cancel_event
        self._cancel_handles = []
        self._state = ControllerState.RUNNING
        worker = threading.Thread(
            target=self._run_worker,
            args=(active, cancel_event, execute),
            name=f"interactive-agent-worker-{generation}",
            daemon=False,
        )
        self._worker = worker
        worker.start()
        return SubmissionOutcome("ACCEPTED", run_generation=generation)

    def submit(self, envelope: SubmissionEnvelope, execute: Executor) -> SubmissionOutcome:
        """Linearize accepted, pending and rejected dispositions under one lock."""

        with self._lock:
            if self._state is ControllerState.IDLE and self._worker is None:
                return self._start_locked(envelope, execute)
            if envelope.busy_submit not in {"PENDING_EXACT_TEXT", "PENDING_TYPED_PAYLOAD"}:
                return SubmissionOutcome("REJECTED_PRESERVE", reason="REQUIRES_IDLE")
            try:
                item = self.pending.add(envelope)
            except PendingCapacityError as exc:
                return SubmissionOutcome("REJECTED_PRESERVE", reason=str(exc))
            return SubmissionOutcome("PENDING_FOLLOWUP", pending_id=item.pending_id)

    def register_cancellation(self, generation: int, callback: Callable[[], None]) -> bool:
        """Bind a canonical runtime handle; a raced request is replayed once."""

        invoke = False
        with self._lock:
            if generation != self._active_generation or self._state is ControllerState.IDLE:
                return False
            if callback not in self._cancel_handles:
                self._cancel_handles.append(callback)
            invoke = bool(self._cancel_event is not None and self._cancel_event.is_set())
        if invoke:
            callback()
        return True

    def mark_waiting_attention(self, generation: int) -> bool:
        with self._lock:
            if generation != self._active_generation or self._state is not ControllerState.RUNNING:
                return False
            self._state = ControllerState.WAITING_ATTENTION
            return True

    def mark_running(self, generation: int) -> bool:
        with self._lock:
            if generation != self._active_generation or self._state is not ControllerState.WAITING_ATTENTION:
                return False
            self._state = ControllerState.RUNNING
            return True

    def request_cancel(self, generation: int | None = None) -> SubmissionOutcome:
        callbacks: tuple[Callable[[], None], ...] = ()
        with self._lock:
            active = self._active_generation
            if active is None or (generation is not None and generation != active):
                return SubmissionOutcome("IGNORED_STALE", reason="STALE_GENERATION")
            if self._state in {ControllerState.SETTLING, ControllerState.TERMINAL, ControllerState.IDLE}:
                return SubmissionOutcome("IGNORED_STALE", run_generation=active, reason="NOT_ACTIVE")
            self._state = ControllerState.CANCELLING
            if self._cancel_event is not None:
                self._cancel_event.set()
            callbacks = tuple(self._cancel_handles)
        for callback in callbacks:
            callback()
        return SubmissionOutcome("CANCEL_REQUESTED", run_generation=active)

    def _run_worker(
        self,
        envelope: SubmissionEnvelope,
        cancel_event: threading.Event,
        execute: Executor,
    ) -> None:
        try:
            def register(callback: Callable[[], None]) -> None:
                self.register_cancellation(envelope.run_generation, callback)

            result = execute(envelope, cancel_event, register)
            error: BaseException | None = None
        except BaseException as exc:  # keep the shell recoverable even for a provider failure
            result = None
            error = exc
        stream_status = self._stream_channel.finish_generation(envelope.run_generation)
        assistant_streamed = stream_status.assistant_seen or bool(
            getattr(result, "assistant_streamed", False)
        )
        assistant_stream_truncated = stream_status.truncated or bool(
            getattr(result, "assistant_stream_truncated", False)
        )
        message = WorkerMessage(
            envelope.run_generation,
            result=result,
            error=error,
            assistant_streamed=assistant_streamed,
            assistant_stream_truncated=assistant_stream_truncated,
        )
        self._result_channel.put(message)

    def poll_result(self) -> WorkerMessage | None:
        try:
            message = self._result_channel.get_nowait()
        except Empty:
            return None
        with self._lock:
            if message.run_generation != self._active_generation:
                self._stale_messages += 1
                return None
            self._last_stream_generation = message.run_generation
            self._state = ControllerState.SETTLING
            self._state = ControllerState.TERMINAL
            self._worker = None
            self._cancel_event = None
            self._cancel_handles = []
            self._active_generation = None
            self._state = ControllerState.IDLE
        return message

    def wait_for_settlement(self, *, timeout_seconds: float = WORKER_SETTLEMENT_TIMEOUT_SECONDS) -> WorkerMessage | None:
        with self._lock:
            worker = self._worker
            generation = self._active_generation
        if worker is None:
            return self.poll_result()
        worker.join(max(0.0, timeout_seconds))
        if worker.is_alive():
            return WorkerMessage(
                generation or 0,
                error=WorkerSettlementTimeout(
                    f"interactive worker did not settle within {timeout_seconds:.3f}s"
                ),
            )
        return self.poll_result()

    def send_pending(self, pending_id: int, execute: Executor) -> SubmissionOutcome:
        with self._lock:
            if self.pending.inspect(pending_id) is None:
                return SubmissionOutcome("IGNORED_DUPLICATE", pending_id=pending_id, reason="PENDING_NOT_FOUND")
            if self._state is not ControllerState.IDLE or self._worker is not None:
                return SubmissionOutcome("REJECTED_PRESERVE", pending_id=pending_id, reason="REQUIRES_IDLE")
            item = self.pending.consume(pending_id)
            assert item is not None
            return self._start_locked(item.envelope, execute)

    def shutdown(
        self,
        *,
        timeout_seconds: float = WORKER_SETTLEMENT_TIMEOUT_SECONDS,
    ) -> WorkerMessage | None:
        """Cancel and join the non-daemon worker before application teardown."""

        with self._lock:
            self._closed = True
            generation = self._active_generation
        if generation is not None:
            self.request_cancel(generation)
        return self.wait_for_settlement(timeout_seconds=timeout_seconds)


__all__ = [
    "ControllerState",
    "InteractiveExecutionController",
    "MAX_INPUT_CHARS",
    "MAX_PENDING_CHARS",
    "MAX_PENDING_ITEMS",
    "PendingCapacityError",
    "PendingItem",
    "PendingStore",
    "SubmissionEnvelope",
    "SubmissionOutcome",
    "WorkerMessage",
    "WorkerSettlementTimeout",
]

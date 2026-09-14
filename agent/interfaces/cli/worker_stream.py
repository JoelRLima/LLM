"""Bounded, generation-bound presentation transport for worker output."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from threading import Lock
from typing import Literal

WorkerStreamKind = Literal["assistant", "diagnostic"]
MAX_WORKER_STREAM_ITEMS = 128
MAX_WORKER_STREAM_CHARS = 64 * 1024
MAX_WORKER_STREAM_CHUNK_CHARS = 4096


@dataclass(frozen=True, slots=True)
class WorkerStreamChunk:
    """One UI-bound worker chunk; the sequence is local to one generation."""

    run_generation: int
    sequence: int
    kind: WorkerStreamKind
    text: str


@dataclass(frozen=True, slots=True)
class WorkerStreamStatus:
    run_generation: int
    assistant_seen: bool
    diagnostic_seen: bool
    truncated: bool
    dropped: int


class BoundedTextBuffer:
    """Small compatibility capture for callers without a controller channel."""

    def __init__(self, *, max_chars: int = MAX_WORKER_STREAM_CHARS) -> None:
        self.max_chars = max(1, int(max_chars))
        self._parts: list[str] = []
        self._size = 0
        self.truncated = False

    def append(self, value: object) -> None:
        text = str(value)
        available = self.max_chars - self._size
        if available <= 0:
            self.truncated = True
            return
        if len(text) > available:
            text = text[:available]
            self.truncated = True
        if text:
            self._parts.append(text)
            self._size += len(text)

    def getvalue(self) -> str:
        return "".join(self._parts)


class BoundedWorkerStream:
    """One non-blocking bounded FIFO shared by a worker and the UI consumer."""

    def __init__(
        self,
        *,
        max_items: int = MAX_WORKER_STREAM_ITEMS,
        max_chars: int = MAX_WORKER_STREAM_CHARS,
    ) -> None:
        self.max_items = max(1, int(max_items))
        self.max_chars = max(1, int(max_chars))
        self._lock = Lock()
        self._generation: int | None = None
        self._accepting = False
        self._chunks: deque[WorkerStreamChunk] = deque()
        self._buffered_chars = 0
        self._next_sequence = 1
        self._assistant_seen = False
        self._diagnostic_seen = False
        self._truncated = False
        self._dropped = 0

    def begin_generation(self, run_generation: int) -> None:
        with self._lock:
            if self._chunks:
                raise RuntimeError("worker stream must be drained before a new generation")
            self._generation = int(run_generation)
            self._accepting = True
            self._buffered_chars = 0
            self._next_sequence = 1
            self._assistant_seen = False
            self._diagnostic_seen = False
            self._truncated = False
            self._dropped = 0

    def _drop_locked(self) -> None:
        self._truncated = True
        self._dropped += 1

    def _append_piece_locked(self, generation: int, kind: WorkerStreamKind, text: str) -> bool:
        if not text:
            return True
        last = self._chunks[-1] if self._chunks else None
        if (
            len(self._chunks) >= self.max_items
            and last is not None
            and last.run_generation == generation
            and last.kind == kind
        ):
            available = min(
                MAX_WORKER_STREAM_CHUNK_CHARS - len(last.text),
                self.max_chars - self._buffered_chars,
            )
            if available > 0:
                addition = text[:available]
                self._chunks[-1] = WorkerStreamChunk(
                    generation,
                    last.sequence,
                    kind,
                    last.text + addition,
                )
                self._buffered_chars += len(addition)
                if len(addition) == len(text):
                    return True
                text = text[len(addition) :]
        if len(self._chunks) >= self.max_items or self._buffered_chars >= self.max_chars:
            self._drop_locked()
            return False
        available = min(
            MAX_WORKER_STREAM_CHUNK_CHARS,
            self.max_chars - self._buffered_chars,
            len(text),
        )
        if available <= 0:
            self._drop_locked()
            return False
        self._chunks.append(
            WorkerStreamChunk(generation, self._next_sequence, kind, text[:available])
        )
        self._next_sequence += 1
        self._buffered_chars += available
        if available < len(text):
            self._drop_locked()
            return False
        return True

    def publish(self, run_generation: int, kind: WorkerStreamKind, value: object) -> bool:
        """Publish without waiting; accepted chunks retain FIFO order."""

        text = str(value)
        if not text:
            return True
        if kind not in {"assistant", "diagnostic"}:
            raise ValueError(f"unknown worker stream kind: {kind}")
        with self._lock:
            if not self._accepting or self._generation != int(run_generation):
                self._dropped += 1
                return False
            accepted = True
            admitted = False
            remaining = text
            while remaining:
                piece = remaining[:MAX_WORKER_STREAM_CHUNK_CHARS]
                remaining = remaining[len(piece) :]
                buffered_before = self._buffered_chars
                if not self._append_piece_locked(int(run_generation), kind, piece):
                    admitted = admitted or self._buffered_chars > buffered_before
                    accepted = False
                    break
                admitted = admitted or self._buffered_chars > buffered_before
            if admitted:
                if kind == "assistant":
                    self._assistant_seen = True
                else:
                    self._diagnostic_seen = True
            return accepted

    def poll(self, run_generation: int | None = None) -> tuple[WorkerStreamChunk, ...]:
        with self._lock:
            generation = self._generation if run_generation is None else int(run_generation)
            if generation is None or generation != self._generation:
                return ()
            result = tuple(self._chunks)
            self._chunks.clear()
            self._buffered_chars = 0
            return result

    def finish_generation(self, run_generation: int) -> WorkerStreamStatus:
        with self._lock:
            generation = int(run_generation)
            if self._generation != generation:
                return WorkerStreamStatus(generation, False, False, False, self._dropped)
            self._accepting = False
            return WorkerStreamStatus(
                generation,
                self._assistant_seen,
                self._diagnostic_seen,
                self._truncated,
                self._dropped,
            )

    def status(self, run_generation: int) -> WorkerStreamStatus:
        with self._lock:
            generation = int(run_generation)
            return WorkerStreamStatus(
                generation,
                self._generation == generation and self._assistant_seen,
                self._generation == generation and self._diagnostic_seen,
                self._generation == generation and self._truncated,
                self._dropped,
            )

    @property
    def pending(self) -> bool:
        with self._lock:
            return bool(self._chunks)


__all__ = [
    "BoundedTextBuffer",
    "BoundedWorkerStream",
    "MAX_WORKER_STREAM_CHARS",
    "MAX_WORKER_STREAM_CHUNK_CHARS",
    "MAX_WORKER_STREAM_ITEMS",
    "WorkerStreamChunk",
    "WorkerStreamKind",
    "WorkerStreamStatus",
]

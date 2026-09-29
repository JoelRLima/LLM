"""Owned lifecycle state for one tool invocation attempt."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from enum import Enum


@dataclass
class InvocationAttempt:
    """Bounded ownership state for one concrete invocation attempt."""

    class Lifecycle(str, Enum):
        ADMITTED = "ADMITTED"
        RUNNING = "RUNNING"
        CANCEL_REQUESTED = "CANCEL_REQUESTED"
        TIMEOUT_REQUESTED = "TIMEOUT_REQUESTED"
        QUIESCING = "QUIESCING"
        QUIESCENT = "QUIESCENT"
        COMMITTED_TERMINAL = "COMMITTED_TERMINAL"

    invocation_id: str
    lock: threading.Lock = field(default_factory=threading.Lock)
    terminal: bool = False
    worker_pending: bool = False
    mutating: bool = False
    lifecycle: Lifecycle = Lifecycle.ADMITTED

    def mark_running(self) -> None:
        with self.lock:
            if not self.terminal:
                self.lifecycle = self.Lifecycle.RUNNING

    def set_mutating(self, value: bool) -> None:
        with self.lock:
            self.mutating = bool(value)

    def request_cancel(self, *, timed_out: bool = False) -> None:
        with self.lock:
            if not self.terminal:
                self.lifecycle = (
                    self.Lifecycle.TIMEOUT_REQUESTED
                    if timed_out
                    else self.Lifecycle.CANCEL_REQUESTED
                )

    def begin_quiescing(self) -> None:
        with self.lock:
            if not self.terminal:
                self.lifecycle = self.Lifecycle.QUIESCING

    def mark_quiescent(self) -> None:
        with self.lock:
            self.worker_pending = False
            if not self.terminal:
                self.lifecycle = self.Lifecycle.QUIESCENT

    def mark_liveness_failure(self) -> None:
        """Close publication ownership without pretending the worker quiesced."""

        with self.lock:
            self.terminal = True
            self.lifecycle = self.Lifecycle.COMMITTED_TERMINAL

    def claim_terminal(self) -> bool:
        with self.lock:
            if self.terminal:
                return False
            if self.mutating and (
                self.worker_pending or self.lifecycle is not self.Lifecycle.QUIESCENT
            ):
                return False
            self.terminal = True
            self.lifecycle = self.Lifecycle.COMMITTED_TERMINAL
            return True

    def mark_worker_pending(self) -> None:
        with self.lock:
            self.worker_pending = True

    def worker_finished(self) -> bool:
        with self.lock:
            self.worker_pending = False
            if not self.terminal:
                self.lifecycle = self.Lifecycle.QUIESCENT
            return self.terminal

    def can_release(self) -> bool:
        with self.lock:
            return self.terminal and not self.worker_pending

    def has_worker_pending(self) -> bool:
        with self.lock:
            return self.worker_pending


_InvocationAttempt = InvocationAttempt

__all__ = ["InvocationAttempt", "_InvocationAttempt"]

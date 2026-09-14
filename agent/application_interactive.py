"""Request-only cancellation support shared by interactive adapters."""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any


class InteractiveCancellationMixin:
    """Expose a UI request signal without taking over task settlement."""

    _closed: bool
    interaction_service: Callable[[], Any]
    orchestrator: Any

    def _init_interactive_cancellation(self) -> None:
        self._interactive_cancellation_lock = threading.Lock()
        self._interactive_cancellation_event: threading.Event | None = None

    def bind_interactive_cancellation(self, event: threading.Event) -> Callable[[], None]:
        with self._interactive_cancellation_lock:
            self._interactive_cancellation_event = event
            self.orchestrator._interactive_cancellation_event = event
        if event.is_set():
            self.request_cancel_only()

        def unbind() -> None:
            with self._interactive_cancellation_lock:
                if self._interactive_cancellation_event is event:
                    self._interactive_cancellation_event = None
                    if getattr(self.orchestrator, "_interactive_cancellation_event", None) is event:
                        self.orchestrator._interactive_cancellation_event = None

        return unbind

    def request_cancel_only(self) -> None:
        with self._interactive_cancellation_lock:
            event = self._interactive_cancellation_event
        if event is not None:
            event.set()
        if self._closed:
            return
        self.interaction_service().cancel_active_model_call()
        self.orchestrator.cancellation_token.cancel()

    def interactive_cancellation_requested(self) -> bool:
        with self._interactive_cancellation_lock:
            event = self._interactive_cancellation_event
        return bool(event is not None and event.is_set())


def apply_interactive_cancellation(application: Any, token: Any) -> None:
    requested = getattr(application, "interactive_cancellation_requested", None)
    if callable(requested) and bool(requested()):
        token.cancel()


def reapply_interactive_cancellation(orchestrator: Any) -> None:
    event = getattr(orchestrator, "_interactive_cancellation_event", None)
    is_set = getattr(event, "is_set", None)
    if event is not None and callable(is_set) and bool(is_set()):
        orchestrator.cancellation_token.cancel()

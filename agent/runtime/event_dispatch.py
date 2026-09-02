"""Canonical RuntimeEvent dispatch and explicit event projections."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any, Protocol

from agent.runtime.events import RuntimeEvent, RuntimeEventKind, serialize_runtime_event
from agent.runtime.logging import logger


class RuntimeEventSink(Protocol):
    def emit(self, event: RuntimeEvent) -> None:
        ...


class StateEventSink:
    """Project canonical events into the legacy AgentState event storage."""

    def __init__(self, state: Any) -> None:
        self.state = state

    def emit(self, event: RuntimeEvent) -> None:
        append_state_event(self.state, event)


def append_state_event(state: Any, event: RuntimeEvent) -> None:
    """Append one canonical runtime event at the state boundary."""

    add_event = getattr(state, "add_event", None)
    if callable(add_event):
        add_event(event)
        return
    events = getattr(state, "events", None)
    if isinstance(events, list):
        events.append(serialize_runtime_event(event))


def dispatch_runtime_event(sink: Any, event: RuntimeEvent) -> None:
    """Send a canonical event to one sink without changing the event fact."""

    if sink is None:
        return
    if isinstance(sink, RuntimeEventDispatcher):
        sink.emit(event)
        return
    try:
        target = sink if callable(sink) else getattr(sink, "emit", None)
        if not callable(target):
            raise TypeError("runtime event sink must expose emit(RuntimeEvent)")
        target(event)
    except Exception as exc:  # event observers cannot change domain behavior
        logger.warning("Runtime event sink failed: %s", type(exc).__name__)


class RuntimeEventDispatcher:
    """Fan out one immutable RuntimeEvent to state and external projections."""

    _CHECKPOINT_EVENTS = frozenset(
        {
            RuntimeEventKind.STEP_COMPLETED,
            RuntimeEventKind.STEP_FAILED,
            RuntimeEventKind.STEP_SKIPPED,
        }
    )

    def __init__(
        self,
        sinks: Iterable[Any] = (),
        *,
        state: Any = None,
        checkpoint_observer: Callable[[RuntimeEvent], None] | None = None,
    ) -> None:
        self._sinks: list[Any] = []
        if state is not None:
            self._sinks.append(StateEventSink(state))
        self._sinks.extend(sinks)
        self._checkpoint_observer = checkpoint_observer

    def add_sink(self, sink: Any) -> None:
        self._sinks.append(sink)

    def emit(self, event: RuntimeEvent) -> None:
        if not isinstance(event, RuntimeEvent):
            raise TypeError("RuntimeEventDispatcher accepts RuntimeEvent only")
        for sink in tuple(self._sinks):
            dispatch_runtime_event(sink, event)
        if self._checkpoint_observer is not None and event.kind in self._CHECKPOINT_EVENTS:
            try:
                self._checkpoint_observer(event)
            except Exception as exc:  # checkpoint policy remains an observer
                logger.warning("Checkpoint event observer failed: %s", type(exc).__name__)


def state_event_projection(event: RuntimeEvent) -> dict[str, Any]:
    """Explicit named adapter for callers that need a serialized event."""

    return serialize_runtime_event(event)


__all__ = [
    "RuntimeEventDispatcher",
    "RuntimeEventSink",
    "StateEventSink",
    "append_state_event",
    "dispatch_runtime_event",
    "state_event_projection",
]

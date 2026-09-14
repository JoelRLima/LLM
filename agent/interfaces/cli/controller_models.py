"""Value objects and bounded pending storage for the execution controller."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

MAX_PENDING_ITEMS = 16
MAX_INPUT_CHARS = 8192
MAX_PENDING_CHARS = 131072


@dataclass(frozen=True)
class SubmissionEnvelope:
    run_generation: int
    visible_text: str
    command_id: str
    routing_kind: str
    busy_policy: str
    busy_submit: str
    payload: str
    boundary: str
    owner: str


@dataclass(frozen=True)
class PendingItem:
    pending_id: int
    envelope: SubmissionEnvelope

    @property
    def visible_text(self) -> str:
        return self.envelope.visible_text


@dataclass(frozen=True)
class SubmissionOutcome:
    disposition: str
    run_generation: int | None = None
    pending_id: int | None = None
    reason: str | None = None


@dataclass(frozen=True)
class WorkerMessage:
    run_generation: int
    result: Any = None
    error: BaseException | None = None
    assistant_streamed: bool = False
    assistant_stream_truncated: bool = False


class PendingCapacityError(ValueError):
    """A pending submission cannot fit; input is preserved by the caller."""


class PendingStore:
    """Bounded session-local pending follow-ups with consume-on-send."""

    def __init__(
        self,
        *,
        max_items: int = MAX_PENDING_ITEMS,
        max_item_chars: int = MAX_INPUT_CHARS,
        max_total_chars: int = MAX_PENDING_CHARS,
    ) -> None:
        self.max_items = max_items
        self.max_item_chars = max_item_chars
        self.max_total_chars = max_total_chars
        self._items: list[PendingItem] = []
        self._next_id = 1

    def _total_chars(self) -> int:
        return sum(len(item.visible_text) for item in self._items)

    def add(self, envelope: SubmissionEnvelope) -> PendingItem:
        if len(envelope.visible_text) > self.max_item_chars or len(envelope.payload) > self.max_item_chars:
            raise PendingCapacityError("PENDING_INPUT_TOO_LARGE")
        if len(self._items) >= self.max_items:
            raise PendingCapacityError("PENDING_QUEUE_FULL")
        if self._total_chars() + len(envelope.visible_text) > self.max_total_chars:
            raise PendingCapacityError("PENDING_TOTAL_LIMIT")
        item = PendingItem(self._next_id, envelope)
        self._next_id += 1
        self._items.append(item)
        return item

    def list(self) -> tuple[PendingItem, ...]:
        return tuple(self._items)

    def inspect(self, pending_id: int) -> PendingItem | None:
        return next((item for item in self._items if item.pending_id == pending_id), None)

    def load_for_editing(self, pending_id: int) -> str | None:
        item = self.inspect(pending_id)
        return item.visible_text if item is not None else None

    def discard(self, pending_id: int) -> bool:
        for index, item in enumerate(self._items):
            if item.pending_id == pending_id:
                del self._items[index]
                return True
        return False

    def consume(self, pending_id: int) -> PendingItem | None:
        for index, item in enumerate(self._items):
            if item.pending_id == pending_id:
                return self._items.pop(index)
        return None


__all__ = [
    "MAX_INPUT_CHARS",
    "MAX_PENDING_CHARS",
    "MAX_PENDING_ITEMS",
    "PendingCapacityError",
    "PendingItem",
    "PendingStore",
    "SubmissionEnvelope",
    "SubmissionOutcome",
    "WorkerMessage",
]

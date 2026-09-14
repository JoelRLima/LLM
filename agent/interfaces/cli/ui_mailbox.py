"""Bounded presentation transport for immutable runtime events."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, replace
from threading import Lock

from agent.runtime.event_kinds import RuntimeEventKind
from agent.runtime.events import RuntimeEvent

_MILESTONE_KINDS = frozenset(
    {
        RuntimeEventKind.PLAN_CREATED,
        RuntimeEventKind.PLAN_EXTENDED,
        RuntimeEventKind.PLAN_PREVIEW_READY,
        RuntimeEventKind.STEP_COMPLETED,
        RuntimeEventKind.STEP_FAILED,
        RuntimeEventKind.STEP_BLOCKED,
        RuntimeEventKind.STEP_CANCELLED,
        RuntimeEventKind.STEP_SKIPPED,
        RuntimeEventKind.STEP_UNVERIFIED,
        RuntimeEventKind.REPLAN,
        RuntimeEventKind.REPLAN_BLOCKED,
        RuntimeEventKind.CONVERGENCE_REPLAN_REQUESTED,
        RuntimeEventKind.CONVERGENCE_REPLAN_DENIED,
        RuntimeEventKind.VALIDATION_REPAIR,
        RuntimeEventKind.TASK_RESUMED,
        RuntimeEventKind.EXECUTION_FRONTIER_PROJECTED,
        RuntimeEventKind.PROGRESS_RECEIPT_ADVANCED,
    }
)
_TERMINAL_KINDS = frozenset({RuntimeEventKind.TASK_OUTCOME, RuntimeEventKind.FINAL})


@dataclass(frozen=True)
class UIEventEnvelope:
    ingestion_sequence: int
    event: RuntimeEvent


@dataclass(frozen=True)
class MailboxStats:
    dropped_milestones: int = 0
    coalesced_updates: int = 0
    replaced_errors: int = 0
    delivered: int = 0


class UIEventMailbox:
    """Nonblocking, bounded presentation transport independent of trace truth."""

    def __init__(self, *, milestone_capacity: int = 64, latest_capacity: int = 16) -> None:
        self.milestone_capacity = max(1, milestone_capacity)
        self.latest_capacity = max(1, latest_capacity)
        self._lock = Lock()
        self._next_sequence = 1
        self._milestones: deque[UIEventEnvelope] = deque(maxlen=self.milestone_capacity)
        self._latest: dict[tuple[str, str], UIEventEnvelope] = {}
        self._latest_order: deque[tuple[str, str]] = deque()
        self._latest_error: UIEventEnvelope | None = None
        self._terminals: dict[str, UIEventEnvelope] = {}
        self._stats = MailboxStats()

    def _emit_error_locked(self, envelope: UIEventEnvelope) -> None:
        if self._latest_error is not None:
            self._stats = replace(self._stats, replaced_errors=self._stats.replaced_errors + 1)
        self._latest_error = envelope

    def _emit_terminal_locked(self, envelope: UIEventEnvelope) -> None:
        self._terminals[envelope.event.run_id] = envelope
        if len(self._terminals) > self.latest_capacity:
            oldest = min(self._terminals, key=lambda run_id: self._terminals[run_id].ingestion_sequence)
            self._terminals.pop(oldest, None)

    def _emit_milestone_locked(self, envelope: UIEventEnvelope) -> None:
        if len(self._milestones) >= self.milestone_capacity:
            self._milestones.popleft()
            self._stats = replace(self._stats, dropped_milestones=self._stats.dropped_milestones + 1)
        self._milestones.append(envelope)

    def _emit_latest_locked(self, envelope: UIEventEnvelope) -> None:
        key = (envelope.event.run_id, envelope.event.kind.value)
        if key in self._latest:
            self._stats = replace(self._stats, coalesced_updates=self._stats.coalesced_updates + 1)
        elif len(self._latest) >= self.latest_capacity:
            old_key = self._latest_order.popleft()
            self._latest.pop(old_key, None)
            self._stats = replace(self._stats, coalesced_updates=self._stats.coalesced_updates + 1)
        self._latest[key] = envelope
        if key not in self._latest_order:
            self._latest_order.append(key)

    def emit(self, event: RuntimeEvent) -> None:
        """Enqueue immutable event and return without waiting for a consumer."""

        if not isinstance(event, RuntimeEvent):
            return
        with self._lock:
            envelope = UIEventEnvelope(self._next_sequence, event)
            self._next_sequence += 1
            kind = event.kind
            if kind is RuntimeEventKind.ERROR:
                self._emit_error_locked(envelope)
                return
            if kind in _TERMINAL_KINDS:
                self._emit_terminal_locked(envelope)
                return
            if kind in _MILESTONE_KINDS:
                self._emit_milestone_locked(envelope)
                return
            self._emit_latest_locked(envelope)

    def drain(self, *, limit: int | None = None) -> tuple[UIEventEnvelope, ...]:
        with self._lock:
            values = list(self._milestones)
            values.extend(self._latest.values())
            values.extend(self._terminals.values())
            if self._latest_error is not None:
                values.append(self._latest_error)
            values.sort(key=lambda item: item.ingestion_sequence)
            delivered_values = values if limit is None else values[: max(0, limit)]
            delivered = len(delivered_values)
            delivered_ids = {item.ingestion_sequence for item in delivered_values}
            self._milestones = deque(
                (item for item in self._milestones if item.ingestion_sequence not in delivered_ids),
                maxlen=self.milestone_capacity,
            )
            self._latest = {
                key: item
                for key, item in self._latest.items()
                if item.ingestion_sequence not in delivered_ids
            }
            self._latest_order = deque(key for key in self._latest_order if key in self._latest)
            self._terminals = {
                run_id: item
                for run_id, item in self._terminals.items()
                if item.ingestion_sequence not in delivered_ids
            }
            if self._latest_error is not None and self._latest_error.ingestion_sequence in delivered_ids:
                self._latest_error = None
            self._stats = MailboxStats(
                self._stats.dropped_milestones,
                self._stats.coalesced_updates,
                self._stats.replaced_errors,
                self._stats.delivered + delivered,
            )
            return tuple(delivered_values)

    def stats(self) -> MailboxStats:
        with self._lock:
            return self._stats

    def __len__(self) -> int:
        with self._lock:
            return len(self._milestones) + len(self._latest) + len(self._terminals) + (1 if self._latest_error else 0)


class RuntimeEventUISink:
    """Presentation-only sink; it never renders or changes canonical state."""

    def __init__(self, mailbox: UIEventMailbox) -> None:
        self.mailbox = mailbox

    def emit(self, event: RuntimeEvent) -> None:
        try:
            self.mailbox.emit(event)
        except Exception:
            return


__all__ = ["MailboxStats", "RuntimeEventUISink", "UIEventEnvelope", "UIEventMailbox"]

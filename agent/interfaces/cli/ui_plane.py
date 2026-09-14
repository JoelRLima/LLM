"""Bounded presentation mailbox and event-derived live view."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Any

from agent.interfaces.cli.ui_mailbox import (
    MailboxStats,
    RuntimeEventUISink,
    UIEventEnvelope,
    UIEventMailbox,
)
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
MAX_STALE_RUN_IDS = 256


@dataclass(frozen=True)
class RunSnapshot:
    generation: int | None
    run_id: str | None
    short_run_id: str | None
    owner: str | None
    state: str
    started_at: str | None
    last_activity_at: str | None
    model_active: bool | None
    current_tool: str | None
    current_invocation_id: str | None
    current_step: str | None
    attention_pending: bool
    pending_count: int
    milestones: tuple[str, ...]
    warning_count: int
    error_count: int
    terminal_outcome: str | None


class RunViewModel:
    """Bounded UI projection sourced only from immutable events/controller state."""

    def __init__(self, *, milestone_capacity: int = 12) -> None:
        self._lock = Lock()
        self._milestone_capacity = max(1, milestone_capacity)
        self._generation: int | None = None
        self._run_id: str | None = None
        self._blocked_run_ids: set[str] = set()
        self._stale_run_order: deque[str] = deque(maxlen=MAX_STALE_RUN_IDS)
        self._owner: str | None = None
        self._state = "IDLE"
        self._terminal_barrier = False
        self._started_at: str | None = None
        self._last_activity_at: str | None = None
        self._model_active: bool | None = None
        self._current_tool: str | None = None
        self._current_invocation_id: str | None = None
        self._current_step: str | None = None
        self._attention_pending = False
        self._pending_count = 0
        self._milestones: deque[str] = deque(maxlen=self._milestone_capacity)
        self._warning_count = 0
        self._error_count = 0
        self._terminal_outcome: str | None = None

    def begin_run(self, generation: int, *, owner: str) -> None:
        with self._lock:
            self._generation = generation
            if self._run_id is not None:
                if self._run_id not in self._blocked_run_ids:
                    self._blocked_run_ids.add(self._run_id)
                    self._stale_run_order.append(self._run_id)
                    if len(self._stale_run_order) == MAX_STALE_RUN_IDS:
                        retained = set(self._stale_run_order)
                        self._blocked_run_ids.intersection_update(retained)
            self._run_id = None
            self._terminal_barrier = False
            self._owner = owner
            self._state = "RUNNING"
            now = datetime.now(timezone.utc).isoformat()
            self._started_at = now
            self._last_activity_at = None
            self._model_active = None
            self._current_tool = None
            self._current_invocation_id = None
            self._current_step = None
            self._attention_pending = False
            self._milestones.clear()
            self._warning_count = 0
            self._error_count = 0
            self._terminal_outcome = None

    def set_pending_count(self, count: int) -> None:
        with self._lock:
            self._pending_count = max(0, int(count))

    def set_attention(self, pending: bool) -> None:
        """Project broker-owned attention; runtime events remain observational."""

        with self._lock:
            if self._terminal_barrier:
                return
            self._attention_pending = bool(pending)
            if self._attention_pending and self._state == "RUNNING":
                self._state = "WAITING_ATTENTION"
            elif not self._attention_pending and self._state == "WAITING_ATTENTION":
                self._state = "RUNNING"

    def _is_current(self, event: RuntimeEvent) -> bool:
        if self._run_id is None:
            self._run_id = event.run_id
            return True
        return event.run_id == self._run_id

    @staticmethod
    def _activity(event: RuntimeEvent) -> str:
        data = event.data
        for key in ("activity", "phase", "tool", "step", "message"):
            value = data.get(key)
            if value is not None and str(value).strip():
                return str(value)
        return event.kind.value

    def _apply_tool_end(self, event: RuntimeEvent) -> None:
        invocation = event.invocation_id or str(event.data.get("invocation_id") or "")
        if self._current_invocation_id in {None, "unknown"} or invocation == self._current_invocation_id:
            self._current_tool = None
            self._current_invocation_id = None

    def _apply_kind(self, event: RuntimeEvent) -> None:
        kind = event.kind
        if kind is RuntimeEventKind.MODEL_CALL_STARTED:
            self._model_active = True
        elif kind is RuntimeEventKind.MODEL_CALL_COMPLETED:
            self._model_active = False
        elif kind is RuntimeEventKind.TOOL_START:
            self._current_tool = str(event.data.get("tool") or "unknown")
            self._current_invocation_id = event.invocation_id or str(event.data.get("invocation_id") or "unknown")
        elif kind is RuntimeEventKind.TOOL_END:
            self._apply_tool_end(event)
        elif kind in {
            RuntimeEventKind.STEP_COMPLETED,
            RuntimeEventKind.STEP_FAILED,
            RuntimeEventKind.STEP_BLOCKED,
            RuntimeEventKind.STEP_CANCELLED,
            RuntimeEventKind.STEP_SKIPPED,
            RuntimeEventKind.STEP_UNVERIFIED,
        }:
            self._current_step = event.step_id or str(event.data.get("step_id") or event.data.get("step") or "unknown")
        elif kind is RuntimeEventKind.WARNING:
            self._warning_count += 1
        elif kind is RuntimeEventKind.ERROR:
            self._error_count += 1
        elif kind in _TERMINAL_KINDS:
            self._terminalize(str(event.data.get("status") or event.data.get("outcome") or kind.value))

    def _record_activity(self, event: RuntimeEvent) -> None:
        kind = event.kind
        if kind in _MILESTONE_KINDS or kind in _TERMINAL_KINDS:
            self._milestones.append(self._activity(event))
        if kind not in {
            RuntimeEventKind.WARNING,
            RuntimeEventKind.CONTEXT_REFRESH,
            RuntimeEventKind.OBSERVATION_REUSE,
            RuntimeEventKind.OBSERVATION_REHYDRATION,
        }:
            self._last_activity_at = event.timestamp

    def apply(self, event: RuntimeEvent) -> bool:
        if not isinstance(event, RuntimeEvent):
            return False
        with self._lock:
            if self._generation is None:
                return False
            if event.run_id in self._blocked_run_ids:
                return False
            if not self._is_current(event):
                return False
            if self._terminal_barrier:
                return False
            self._apply_kind(event)
            if event.kind in _TERMINAL_KINDS:
                self._terminal_barrier = True
            self._record_activity(event)
            return True

    def apply_result(self, generation: int, result: Any) -> bool:
        with self._lock:
            if self._generation != generation:
                return False
            status = getattr(result, "status", None)
            outcome = getattr(status, "value", status) or getattr(result, "summary", None) or "completed"
            self._terminalize(str(outcome))
            return True

    def apply_error(self, generation: int, error: BaseException) -> bool:
        with self._lock:
            if self._generation != generation:
                return False
            self._terminalize(f"error: {type(error).__name__}")
            self._error_count += 1
            return True

    def _terminalize(self, outcome: str) -> None:
        self._state = "TERMINAL"
        self._terminal_barrier = True
        self._model_active = False
        self._current_tool = None
        self._current_invocation_id = None
        self._current_step = None
        self._attention_pending = False
        self._terminal_outcome = outcome

    def snapshot(self) -> RunSnapshot:
        with self._lock:
            short = self._run_id[:8] if self._run_id else None
            return RunSnapshot(
                self._generation,
                self._run_id,
                short,
                self._owner,
                self._state,
                self._started_at,
                self._last_activity_at,
                self._model_active,
                self._current_tool,
                self._current_invocation_id,
                self._current_step,
                self._attention_pending,
                self._pending_count,
                tuple(self._milestones),
                self._warning_count,
                self._error_count,
                self._terminal_outcome,
            )

    def render_toolbar(self, *, width: int = 120, mode: str = "FULL") -> str:
        snap = self.snapshot()
        if snap.state == "IDLE" or snap.generation is None:
            return f"Ready | mode={mode}"
        run = snap.short_run_id or f"gen-{snap.generation}"
        activity = snap.current_tool or snap.current_step or (snap.milestones[-1] if snap.milestones else "working")
        attention = " | ATTENTION" if snap.attention_pending else ""
        pending = f" | pending={snap.pending_count}" if snap.pending_count else ""
        full = f"RUN {run} | {snap.state} {activity}{attention}{pending} | mode={mode}"
        if width >= 120:
            return full
        if width >= 80:
            return f"RUN {run} | {snap.state} {activity}{attention}"
        return f"{snap.state} {activity}{attention}"[: max(1, width)]


__all__ = [
    "MailboxStats",
    "RunSnapshot",
    "RunViewModel",
    "RuntimeEventUISink",
    "UIEventEnvelope",
    "UIEventMailbox",
]

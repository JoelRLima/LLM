"""Small lock-protected active-invocation set shared by the gateway."""

from __future__ import annotations

import time
from typing import Any


class InvocationActivityMixin:
    _invocation_lock: Any
    _active_invocations: set[str]
    _active_invocation_meta: dict[str, dict[str, Any]]
    _activity_condition: Any

    def _begin_invocation(
        self,
        invocation_id: str,
        *,
        task_id: str | None = None,
        mutating: bool = False,
        cancellation_event: Any = None,
    ) -> bool:
        with self._invocation_lock:
            if invocation_id in self._active_invocations or invocation_id in getattr(
                self, "_canonical_commit_failed", set()
            ):
                return False
            self._active_invocations.add(invocation_id)
            self._active_invocation_meta[invocation_id] = {
                "task_id": task_id,
                "mutating": bool(mutating),
                "cancellation_event": cancellation_event,
            }
            return True

    def _set_invocation_mutating(self, invocation_id: str, mutating: bool) -> None:
        with self._invocation_lock:
            metadata = self._active_invocation_meta.get(invocation_id)
            if metadata is not None:
                metadata["mutating"] = bool(mutating)

    def _finish_invocation(self, invocation_id: str) -> None:
        with self._invocation_lock:
            self._active_invocations.discard(invocation_id)
            self._active_invocation_meta.pop(invocation_id, None)
            self._activity_condition.notify_all()

    def active_invocation_ids(
        self,
        *,
        task_id: str | None = None,
        mutating_only: bool = False,
    ) -> tuple[str, ...]:
        with self._invocation_lock:
            return tuple(
                invocation_id
                for invocation_id in sorted(self._active_invocations)
                if (
                    (task_id is None or self._active_invocation_meta.get(invocation_id, {}).get("task_id") == task_id)
                    and (
                        not mutating_only
                        or self._active_invocation_meta.get(invocation_id, {}).get("mutating") is True
                    )
                )
            )

    def are_invocations_quiescent(
        self,
        *,
        task_id: str | None = None,
        mutating_only: bool = False,
    ) -> bool:
        return not self.active_invocation_ids(task_id=task_id, mutating_only=mutating_only)

    def request_invocation_cancellation(
        self,
        *,
        task_id: str | None = None,
        mutating_only: bool = False,
    ) -> tuple[str, ...]:
        with self._invocation_lock:
            selected = tuple(
                invocation_id
                for invocation_id in sorted(self._active_invocations)
                if (
                    (task_id is None or self._active_invocation_meta.get(invocation_id, {}).get("task_id") == task_id)
                    and (
                        not mutating_only
                        or self._active_invocation_meta.get(invocation_id, {}).get("mutating") is True
                    )
                )
            )
            for invocation_id in selected:
                event = self._active_invocation_meta[invocation_id].get("cancellation_event")
                if event is not None and callable(getattr(event, "set", None)):
                    event.set()
            return selected

    def drain_invocations(
        self,
        timeout_seconds: float | None = 5.0,
        *,
        task_id: str | None = None,
        mutating_only: bool = False,
    ) -> bool:
        """Wait for actual owned workers to leave the active set."""

        deadline = None if timeout_seconds is None else time.monotonic() + max(0.0, timeout_seconds)
        with self._activity_condition:
            while True:
                active = tuple(
                    invocation_id
                    for invocation_id in sorted(self._active_invocations)
                    if (
                        (task_id is None or self._active_invocation_meta.get(invocation_id, {}).get("task_id") == task_id)
                        and (
                            not mutating_only
                            or self._active_invocation_meta.get(invocation_id, {}).get("mutating") is True
                        )
                    )
                )
                if not active:
                    return True
                if deadline is None:
                    self._activity_condition.wait()
                    continue
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._activity_condition.wait(timeout=remaining)

"""Small lock-protected active-invocation set shared by the gateway."""

from __future__ import annotations

from typing import Any


class InvocationActivityMixin:
    _invocation_lock: Any
    _active_invocations: set[str]

    def _begin_invocation(self, invocation_id: str) -> bool:
        with self._invocation_lock:
            if invocation_id in self._active_invocations:
                return False
            self._active_invocations.add(invocation_id)
            return True

    def _finish_invocation(self, invocation_id: str) -> None:
        with self._invocation_lock:
            self._active_invocations.discard(invocation_id)

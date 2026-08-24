"""Cancellation projection from a gateway invocation into a code-task context."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from agent.cancellation import is_cancellation_requested


class InvocationCancellationView:
    """Read-only cancellation view combining task and gateway ownership."""

    def __init__(self, parent: Any, token: Any | None, event: Any | None) -> None:
        self._parent = parent
        self._token = token
        self._event = event

    @property
    def cancelled(self) -> bool:
        return bool(
            getattr(self._parent, "cancelled", False)
            or is_cancellation_requested(self._token, self._event)
        )


def with_invocation_cancellation(
    context: Any | None,
    token: Any | None,
    event: Any | None,
) -> Any | None:
    if context is None or (token is None and event is None):
        return context
    return replace(
        context,
        cancellation=InvocationCancellationView(context.cancellation, token, event),
    )


__all__ = ["with_invocation_cancellation"]

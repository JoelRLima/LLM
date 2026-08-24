"""Owned invocation shutdown for the application lifecycle."""

from __future__ import annotations

from typing import Any

from agent.tools.invocation_execution import InvocationLivenessError


def drain_application_invocations(gateway: Any) -> bool:
    """Request bounded shutdown and report whether owned work quiesced."""

    request_cancel = getattr(gateway, "request_invocation_cancellation", None)
    if callable(request_cancel):
        request_cancel()
    drain = getattr(gateway, "drain_invocations", None)
    if callable(drain):
        return drain(timeout_seconds=5.0) is not False
    return True


def require_application_invocations_drained(gateway: Any) -> None:
    if not drain_application_invocations(gateway):
        raise InvocationLivenessError(
            "application shutdown cannot close while an invocation remains active"
        )


__all__ = ["drain_application_invocations", "require_application_invocations_drained"]

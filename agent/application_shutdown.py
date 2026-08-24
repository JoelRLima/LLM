"""Owned invocation shutdown for the application lifecycle."""

from __future__ import annotations

from typing import Any


def drain_application_invocations(gateway: Any) -> None:
    request_cancel = getattr(gateway, "request_invocation_cancellation", None)
    if callable(request_cancel):
        request_cancel()
    drain = getattr(gateway, "drain_invocations", None)
    if callable(drain):
        drain(timeout_seconds=5.0)


__all__ = ["drain_application_invocations"]

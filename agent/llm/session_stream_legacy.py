"""Public two-phase stream compatibility boundary."""
from __future__ import annotations

from typing import Any, Callable, Dict, cast

from agent.llm.contracts import PendingStream


def send_legacy_request(
    session: Any, payload: Dict[str, Any], *, stream: bool
) -> Any:
    """Delegate legacy send and reservation semantics to ModelCallService."""

    from agent.runtime.model_call import ModelCallService

    return ModelCallService.for_session(session).start_legacy_request(
        payload, stream=stream
    )


def send_legacy_stream_request(session: Any, payload: Dict[str, Any]) -> PendingStream:
    """Keep the public two-phase legacy stream envelope at one boundary."""

    return cast(PendingStream, send_legacy_request(session, payload, stream=True))


def process_legacy_stream(
    session: Any, response: Any, callbacks: Dict[str, Callable]
) -> str:
    """Delegate legacy stream consumption and finalization to ModelCallService."""

    from agent.runtime.model_call import ModelCallService

    service = (
        response.service
        if isinstance(response, PendingStream)
        and isinstance(getattr(response, "service", None), ModelCallService)
        else ModelCallService.for_session(session)
    )
    if isinstance(response, PendingStream):
        return service.consume_pending(response, callbacks).text
    return service.consume_external_stream(response, callbacks)


__all__ = ["process_legacy_stream", "send_legacy_request", "send_legacy_stream_request"]

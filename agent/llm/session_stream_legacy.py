"""Public two-phase stream compatibility boundary."""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, cast

from agent.llm.contracts import PendingStream, response_usage
from agent.runtime.budget import (
    BudgetExhausted,
    estimate_payload_allowance,
    estimate_payload_tokens,
)


def send_legacy_request(
    session: Any, payload: Dict[str, Any], *, stream: bool
) -> Any:
    """Send raw legacy transport with exactly one task-budget reservation."""

    call_number = session.budget_ledger.reserve_model_call(
        estimate_payload_allowance(
            payload,
            getattr(session.gateway, "count_tokens", None),
        )
    )
    started_at = time.monotonic()
    try:
        response = session.gateway.send_payload(payload, stream=stream)
    except BudgetExhausted:
        estimate = estimate_payload_tokens(payload)
        session._finalize_model_call(
            call_number,
            started_at,
            success=False,
            streaming=stream,
            estimated_tokens=estimate,
        )
        raise
    except BaseException:
        estimate = estimate_payload_tokens(payload)
        session._finalize_model_call(
            call_number,
            started_at,
            success=False,
            streaming=stream,
            estimated_tokens=estimate,
        )
        raise
    if stream:
        return PendingStream(response, call_number, payload, started_at)

    session._finalize_model_call(
        call_number,
        started_at,
        success=True,
        streaming=False,
        response=response,
        usage=response_usage(response),
        estimated_tokens=estimate_payload_tokens(payload),
    )
    return response


def send_legacy_stream_request(session: Any, payload: Dict[str, Any]) -> PendingStream:
    """Keep the public two-phase legacy stream envelope at one boundary."""

    return cast(PendingStream, send_legacy_request(session, payload, stream=True))


def process_legacy_stream(
    session: Any, response: Any, callbacks: Dict[str, Callable]
) -> str:
    """Consume the public legacy stream envelope and finalize once."""

    if not isinstance(response, PendingStream):
        return cast(str, session.gateway.consume_stream(response, callbacks))

    usage: Any = None

    def capture_usage(value: Any) -> None:
        nonlocal usage
        usage = value
        callback = callbacks.get("on_usage")
        if callback is not None:
            callback(value)

    stream_callbacks = dict(callbacks)
    stream_callbacks["on_usage"] = capture_usage
    visible = ""
    try:
        visible = cast(
            str,
            session.gateway.consume_stream(response.response, stream_callbacks),
        )
    except BudgetExhausted as exc:
        partial_content = getattr(exc, "partial_content", None)
        visible = partial_content if isinstance(partial_content, str) else visible
        estimate = estimate_payload_tokens(response.payload, visible)
        session._finalize_model_call(
            response.call_number,
            response.started_at,
            success=False,
            streaming=True,
            response={"usage": usage} if usage is not None else None,
            usage=usage,
            estimated_tokens=estimate,
        )
        raise
    except BaseException as exc:
        partial_content = getattr(exc, "partial_content", None)
        visible = partial_content if isinstance(partial_content, str) else visible
        estimate = estimate_payload_tokens(response.payload, visible)
        session._finalize_model_call(
            response.call_number,
            response.started_at,
            success=False,
            streaming=True,
            response={"usage": usage} if usage is not None else None,
            usage=usage,
            estimated_tokens=estimate,
        )
        raise
    estimate = estimate_payload_tokens(response.payload, visible)
    observed = {"usage": usage} if usage is not None else visible
    session._finalize_model_call(
        response.call_number,
        response.started_at,
        success=True,
        streaming=True,
        response=observed,
        usage=usage,
        estimated_tokens=estimate,
    )
    return visible


__all__ = ["process_legacy_stream", "send_legacy_request", "send_legacy_stream_request"]

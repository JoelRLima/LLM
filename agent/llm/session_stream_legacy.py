"""Public two-phase stream compatibility boundary."""

from __future__ import annotations

import time
from dataclasses import replace
from typing import Any, Callable, Dict, cast

from agent.llm.contracts import PendingStream, response_usage
from agent.llm.legacy_payload import legacy_payload
from agent.runtime.budget import (
    BudgetExhausted,
    estimate_model_request_tokens,
)
from agent.runtime.budget_estimation import measure_model_request_input_tokens


def send_legacy_request(
    session: Any, payload: Dict[str, Any], *, stream: bool
) -> Any:
    """Send raw legacy transport with exactly one task-budget reservation."""

    request = replace(session.build_legacy_request(payload), stream=stream)
    request_input_measurement = measure_model_request_input_tokens(
        request, session.gateway
    )
    estimated_request_tokens = request_input_measurement.token_count or 0
    estimation_source = request_input_measurement.source
    allowance = max(1, estimated_request_tokens + max(0, request.max_output_tokens))
    dispatch_payload = payload
    if callable(getattr(session.gateway, "build_payload", None)):
        # Keep the legacy transport aligned with the canonical payload owner
        # used by the request-level counter and the modern dispatch path.
        dispatch_payload = legacy_payload(session.gateway, request)
    call_number = session.budget_ledger.reserve_model_call(allowance)
    started_at = time.monotonic()
    try:
        response = session.gateway.send_payload(dispatch_payload, stream=stream)
    except BudgetExhausted:
        estimate = estimate_model_request_tokens(
            request,
            request_input_measurement=request_input_measurement,
            gateway=session.gateway,
        )
        session._finalize_model_call(
            call_number,
            started_at,
            success=False,
            streaming=stream,
            estimated_tokens=estimate,
            estimated_request_tokens=estimated_request_tokens,
            request_estimation_source=estimation_source,
            request_input_measurement=request_input_measurement,
            request=request,
        )
        raise
    except BaseException:
        estimate = estimate_model_request_tokens(
            request,
            request_input_measurement=request_input_measurement,
            gateway=session.gateway,
        )
        session._finalize_model_call(
            call_number,
            started_at,
            success=False,
            streaming=stream,
            estimated_tokens=estimate,
            estimated_request_tokens=estimated_request_tokens,
            request_estimation_source=estimation_source,
            request_input_measurement=request_input_measurement,
            request=request,
        )
        raise
    if stream:
        return PendingStream(
            response,
            call_number,
            dispatch_payload,
            started_at,
            request,
            request_input_measurement,
        )

    session._finalize_model_call(
        call_number,
        started_at,
        success=True,
        streaming=False,
        response=response,
        usage=response_usage(response),
        estimated_tokens=estimate_model_request_tokens(
            request,
            response,
            request_input_measurement=request_input_measurement,
            gateway=session.gateway,
        ),
        estimated_request_tokens=estimated_request_tokens,
        request_estimation_source=estimation_source,
        request_input_measurement=request_input_measurement,
        request=request,
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
    request = response.request
    request_input_measurement = response.request_input_measurement
    if request is None:
        request = replace(session.build_legacy_request(response.payload), stream=True)
    if request_input_measurement is None:
        request_input_measurement = measure_model_request_input_tokens(
            request, session.gateway
        )
    estimated_request_tokens = request_input_measurement.token_count or 0
    estimation_source = request_input_measurement.source

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
        estimate = estimate_model_request_tokens(
            request,
            visible,
            request_input_measurement=request_input_measurement,
            gateway=session.gateway,
            usage=usage,
        )
        session._finalize_model_call(
            response.call_number,
            response.started_at,
            success=False,
            streaming=True,
            response={"usage": usage} if usage is not None else None,
            usage=usage,
            estimated_tokens=estimate,
            estimated_request_tokens=estimated_request_tokens,
            request_estimation_source=estimation_source,
            request_input_measurement=request_input_measurement,
            request=request,
        )
        raise
    except BaseException as exc:
        partial_content = getattr(exc, "partial_content", None)
        visible = partial_content if isinstance(partial_content, str) else visible
        estimate = estimate_model_request_tokens(
            request,
            visible,
            request_input_measurement=request_input_measurement,
            gateway=session.gateway,
            usage=usage,
        )
        session._finalize_model_call(
            response.call_number,
            response.started_at,
            success=False,
            streaming=True,
            response={"usage": usage} if usage is not None else None,
            usage=usage,
            estimated_tokens=estimate,
            estimated_request_tokens=estimated_request_tokens,
            request_estimation_source=estimation_source,
            request_input_measurement=request_input_measurement,
            request=request,
        )
        raise
    estimate = estimate_model_request_tokens(
        request,
        visible,
        request_input_measurement=request_input_measurement,
        gateway=session.gateway,
        usage=usage,
    )
    observed = {"usage": usage} if usage is not None else visible
    session._finalize_model_call(
        response.call_number,
        response.started_at,
        success=True,
        streaming=True,
        response=observed,
        usage=usage,
        estimated_tokens=estimate,
        estimated_request_tokens=estimated_request_tokens,
        request_estimation_source=estimation_source,
        request_input_measurement=request_input_measurement,
        request=request,
    )
    return visible


__all__ = ["process_legacy_stream", "send_legacy_request", "send_legacy_stream_request"]

"""Compatibility send/consume adapters backed by the shared call lifecycle."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import Any, Dict, cast

from agent.llm.contracts import ModelResponse, PendingStream, TokenUsage, response_usage
from agent.llm.legacy_payload import legacy_payload
from agent.runtime.budget_estimation import RequestInputMeasurement
from agent.runtime.model_call_stream import (
    consume_events,
    consume_legacy_response,
    native_events,
    observed_stream_response,
)


def start_legacy_request(
    service: Any,
    payload: Dict[str, Any],
    *,
    stream: bool,
    operation: str,
) -> Any:
    if service.session is None:
        raise RuntimeError("legacy model-call transport requires a ChatSession")
    request = replace(service.session.build_legacy_request(payload), stream=stream)
    measurement, call_number, reserved_tokens, started_at = service._admit(request)
    dispatch_payload = payload
    if callable(getattr(service.gateway, "build_payload", None)):
        dispatch_payload = legacy_payload(service.gateway, request)
    service._start_event(operation, call_number)
    try:
        with service.context.model_slot():
            send_payload = getattr(service.gateway, "send_payload", None)
            if callable(send_payload):
                response = send_payload(dispatch_payload, stream=stream)
            elif stream and callable(getattr(service.gateway, "stream", None)):
                response = iter(native_events(service.gateway, request))
            else:
                response = service._complete_provider(request)
    except BaseException:
        estimate = service._estimate(request, None, measurement, None)
        service._record(
            request=request,
            started_at=started_at,
            call_number=call_number,
            reserved_tokens=reserved_tokens,
            measurement=measurement,
            estimated_tokens=estimate,
            success=False,
            streaming=stream,
            response=None,
            usage=None,
            operation=operation,
        )
        raise
    if stream:
        return PendingStream(
            response,
            call_number,
            dispatch_payload,
            started_at,
            request,
            measurement,
            service=service,
            operation=operation,
        )
    usage = response_usage(response)
    estimate = service._estimate(request, response, measurement, usage)
    service._record(
        request=request,
        started_at=started_at,
        call_number=call_number,
        reserved_tokens=reserved_tokens,
        measurement=measurement,
        estimated_tokens=estimate,
        success=True,
        streaming=False,
        response=response,
        usage=usage,
        operation=operation,
    )
    return response


def consume_pending_stream(
    service: Any,
    pending: PendingStream,
    callbacks: Dict[str, Callable[..., Any]],
) -> Any:
    request = pending.request
    if request is None:
        raise ValueError("pending stream has no canonical request")
    measurement = pending.request_input_measurement
    if not isinstance(measurement, RequestInputMeasurement):
        measurement = service.context.measure_request_input_tokens(request)
    operation = pending.operation or "legacy_request"
    usage: Any = None
    visible = ""
    try:
        with service.context.model_slot():
            if callable(getattr(service.gateway, "consume_stream", None)):
                visible, usage = consume_legacy_response(
                    service.gateway, pending.response, callbacks
                )
            else:
                visible, usage = consume_events(
                    pending.response,
                    callbacks,
                )
    except BaseException as exc:
        captured_usage = getattr(exc, "stream_usage", None)
        if captured_usage is not None:
            usage = captured_usage
        partial = getattr(exc, "partial_content", None)
        if isinstance(partial, str) and partial:
            visible = partial
        observed = observed_stream_response(visible, usage)
        estimate = service._estimate(request, observed, measurement, usage)
        service._record(
            request=request,
            started_at=pending.started_at,
            call_number=pending.call_number,
            reserved_tokens=service.context.reservation_for_model_call(pending.call_number),
            measurement=measurement,
            estimated_tokens=estimate,
            success=False,
            streaming=True,
            response=observed,
            usage=usage,
            operation=operation,
        )
        raise
    observed = observed_stream_response(visible, usage)
    estimate = service._estimate(request, observed, measurement, usage)
    record = service._record(
        request=request,
        started_at=pending.started_at,
        call_number=pending.call_number,
        reserved_tokens=service.context.reservation_for_model_call(pending.call_number),
        measurement=measurement,
        estimated_tokens=estimate,
        success=True,
        streaming=True,
        response=observed,
        usage=usage,
        operation=operation,
    )
    return service._outcome(
        ModelResponse(
            content=visible,
            usage=usage if usage is not None else TokenUsage(available=False),
        ),
        record,
        pending.call_number,
        usage,
        visible,
    )


def consume_external_stream(
    service: Any,
    response: Any,
    callbacks: Dict[str, Callable[..., Any]],
) -> str:
    consume = getattr(service.gateway, "consume_stream", None)
    if callable(consume):
        return cast(str, consume(response, callbacks))
    return consume_events(response, callbacks)[0]


__all__ = [
    "consume_external_stream",
    "consume_pending_stream",
    "start_legacy_request",
]

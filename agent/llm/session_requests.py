"""Canonical request/stream operations for :class:`ChatSession`."""
from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import Any, Dict, Optional, cast

from agent.llm.contracts import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ModelResponseError,
    StreamEventType,
    StructuredOutputMode,
    StructuredOutputRequest,
    response_text,
)
from agent.llm.decision_contract import ModelRequestContract, coerce_request_contract
from agent.llm.legacy_payload import (
    build_legacy_model_request,
    complete_legacy_payload_request,
    legacy_payload_from_request,
)
from agent.llm.legacy_payload import (
    legacy_payload as _legacy_payload,
)
from agent.runtime.budget import (
    BudgetExhausted,
    estimate_model_request_tokens,
)
from agent.runtime.budget_estimation import measure_model_request_input_tokens


def _mapping_value(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}
def _session_config(session: Any) -> Mapping[str, Any]:
    return _mapping_value(getattr(session, "config", {}))
def _gateway_profile(session: Any) -> Mapping[str, Any]:
    return _mapping_value(getattr(getattr(session, "gateway", None), "profile", {}))
def _integer(value: Any, default: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
def build_model_request(
    session: Any,
    response_format: Optional[str] = None,
    grammar: Optional[str] = None,
    *,
    stream: bool = True,
    max_output_tokens: int | None = None,
    request_contract: ModelRequestContract | str | None = None,
) -> ModelRequest:
    system_content = session.get_effective_system_prompt()
    if response_format:
        system_content += "\n\n" + response_format
    payload_messages = [{"role": "system", "content": system_content}] + session.messages[1:]
    structured = (
        StructuredOutputRequest(mode=StructuredOutputMode.GBNF, grammar=grammar)
        if grammar is not None
        else None
    )
    config = _session_config(session)
    profile = _gateway_profile(session)
    hardware_profile = getattr(session, "hardware_profile", None)
    configured_output_tokens = _integer(
        profile.get(
            "max_tokens",
            config.get(
                "max_tokens",
                getattr(hardware_profile, "default_output_tokens", 1024),
            ),
        ),
        1024,
    )
    return ModelRequest(
        messages=tuple(
            ModelMessage(role=message["role"], content=message["content"])
            for message in payload_messages
        ),
        model=str(getattr(session.gateway, "model", config.get("model", "default"))),
        temperature=float(
            profile.get("temperature", config.get("temperature", 0.6))
        ),
        max_output_tokens=(
            configured_output_tokens
            if max_output_tokens is None
            else int(max_output_tokens)
        ),
        stream=stream,
        reasoning_budget=_integer(getattr(session, "thinking_budget", 0), 0),
        structured_output=structured,
        context_limit=getattr(hardware_profile, "context_limit", None),
        request_contract=coerce_request_contract(request_contract),
    )
def complete_model_request(session: Any, request: ModelRequest) -> ModelResponse:
    """Complete one canonical request with task-budget accounting."""
    estimated_request_tokens, estimation_source = measure_model_request_input_tokens(
        request, getattr(session.gateway, "count_tokens", None)
    )
    allowance = max(1, estimated_request_tokens + max(0, request.max_output_tokens))
    call_number = session.budget_ledger.reserve_model_call(allowance)
    started_at = time.monotonic()
    try:
        if callable(getattr(session.gateway, "complete", None)):
            response = session.gateway.complete(request)
        else:
            payload = _legacy_payload(session.gateway, request)
            legacy_response = session.gateway.complete_payload(payload)
            response = (
                legacy_response
                if isinstance(legacy_response, ModelResponse)
                else ModelResponse(content=response_text(legacy_response))
            )
        if not isinstance(response, ModelResponse):
            response = ModelResponse(content=response_text(response))
    except BudgetExhausted:
        estimate = estimate_model_request_tokens(request)
        session._finalize_model_call(
            call_number, started_at, success=False, streaming=False,
            estimated_tokens=estimate, estimated_request_tokens=estimated_request_tokens,
            request_estimation_source=estimation_source, context_compacted=request.context_compacted,
            request=request,
        )
        raise
    except BaseException:
        estimate = estimate_model_request_tokens(request)
        session._finalize_model_call(
            call_number, started_at, success=False, streaming=False,
            estimated_tokens=estimate, estimated_request_tokens=estimated_request_tokens,
            request_estimation_source=estimation_source, context_compacted=request.context_compacted,
            request=request,
        )
        raise
    estimate = estimate_model_request_tokens(request, response)
    session._finalize_model_call(
        call_number,
        started_at,
        success=True,
        streaming=False,
        response=response,
        usage=response.usage,
        estimated_tokens=estimate,
        estimated_request_tokens=estimated_request_tokens,
        request_estimation_source=estimation_source,
        context_compacted=request.context_compacted,
        request=request,
    )
    return response
def _callback(callbacks: Dict[str, Callable[..., Any]], name: str, value: Any) -> None:
    handler = callbacks.get(name)
    if handler is not None:
        handler(value)
def _consume_gateway_events(
    gateway: Any,
    request: ModelRequest,
    callbacks: Dict[str, Callable[..., Any]],
) -> tuple[str, Any]:
    """Consume native events while keeping the event loop out of ChatSession."""
    raw_callback = callbacks.get("on_raw_line")
    if raw_callback is not None:
        raw_callback("")
    visible = ""
    usage: Any = None
    for event in gateway.stream(request):
        if event.type is StreamEventType.REASONING:
            _callback(callbacks, "on_thinking_chunk", event.text)
        elif event.type is StreamEventType.CONTENT:
            visible += event.text
            _callback(callbacks, "on_content_chunk", event.text)
        elif event.type is StreamEventType.USAGE:
            usage = event.data
            _callback(callbacks, "on_usage", event.data)
        elif event.type is StreamEventType.ERROR:
            _callback(callbacks, "on_error", event.text)
            raise ModelResponseError(event.text, partial_content=visible)
        elif event.type is StreamEventType.DONE and event.data:
            _callback(callbacks, "on_done", event.data)
    return visible, usage
def _consume_legacy_gateway(
    gateway: Any,
    request: ModelRequest,
    callbacks: Dict[str, Callable[..., Any]],
) -> tuple[str, Any]:
    payload = _legacy_payload(gateway, request)
    raw_response = gateway.send_payload(payload, stream=True)
    usage: Any = None
    def capture_usage(value: Any) -> None:
        nonlocal usage
        usage = value
        _callback(callbacks, "on_usage", value)
    stream_callbacks = dict(callbacks)
    stream_callbacks["on_usage"] = capture_usage
    visible = cast(str, gateway.consume_stream(raw_response, stream_callbacks))
    return visible, usage
def consume_model_stream(
    session: Any,
    request: ModelRequest,
    callbacks: Dict[str, Callable[..., Any]],
) -> str:
    """Consume a native or legacy stream with one ledger reservation."""
    request = replace(request, stream=True)
    estimated_request_tokens, estimation_source = measure_model_request_input_tokens(
        request, getattr(session.gateway, "count_tokens", None)
    )
    allowance = max(1, estimated_request_tokens + max(0, request.max_output_tokens))
    call_number = session.budget_ledger.reserve_model_call(allowance)
    started_at = time.monotonic()
    usage: Any = None
    visible = ""
    try:
        if callable(getattr(session.gateway, "stream", None)):
            visible, usage = _consume_gateway_events(session.gateway, request, callbacks)
        else:
            visible, usage = _consume_legacy_gateway(session.gateway, request, callbacks)
    except BudgetExhausted as exc:
        partial_content = getattr(exc, "partial_content", None)
        if isinstance(partial_content, str) and partial_content:
            visible = partial_content
        estimate = estimate_model_request_tokens(request, visible)
        session._finalize_model_call(
            call_number,
            started_at,
            success=False,
            streaming=True,
            response={"usage": usage} if usage is not None else visible,
            usage=usage,
            estimated_tokens=estimate,
            estimated_request_tokens=estimated_request_tokens,
            request_estimation_source=estimation_source,
            context_compacted=request.context_compacted,
            request=request,
        )
        raise
    except BaseException as exc:
        partial_content = getattr(exc, "partial_content", None)
        if isinstance(partial_content, str) and partial_content:
            visible = partial_content
        estimate = estimate_model_request_tokens(request, visible)
        session._finalize_model_call(
            call_number,
            started_at,
            success=False,
            streaming=True,
            response={"usage": usage} if usage is not None else visible,
            usage=usage,
            estimated_tokens=estimate,
            estimated_request_tokens=estimated_request_tokens,
            request_estimation_source=estimation_source,
            context_compacted=request.context_compacted,
            request=request,
        )
        raise
    estimate = estimate_model_request_tokens(request, visible)
    session._finalize_model_call(
        call_number,
        started_at,
        success=True,
        streaming=True,
        response={"usage": usage} if usage is not None else visible,
        usage=usage,
        estimated_tokens=estimate,
        estimated_request_tokens=estimated_request_tokens,
        request_estimation_source=estimation_source,
        context_compacted=request.context_compacted,
        request=request,
    )
    return visible.strip()
__all__ = [
    "build_legacy_model_request",
    "build_model_request",
    "complete_legacy_payload_request",
    "complete_model_request",
    "consume_model_stream",
    "legacy_payload_from_request",
]

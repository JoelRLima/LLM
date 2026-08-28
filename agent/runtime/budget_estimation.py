"""Fallback token accounting around the canonical request measurement."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent.llm.contracts import normalize_usage
from agent.runtime.request_measurement import (
    HEURISTIC_CHARS_PER_TOKEN,
    PROVIDER_CHAT_INPUT_TOKENS,
    PROVIDER_TEXT_TOKENIZER,
    UNAVAILABLE,
    RequestInputMeasurement,
    _available_count,
    _gateway_text_token_counter,
    _provider_text_measurement,
    measure_model_request_input_tokens,
    measure_payload_input_tokens,
    measure_request_input_tokens_from_texts,
)


def _response_text(response: Any) -> str:
    if isinstance(response, str):
        return response
    if isinstance(response, Mapping):
        content = response.get("content")
        return content if isinstance(content, str) else ""
    content = getattr(response, "content", None)
    return content if isinstance(content, str) else ""


def _response_usage(response: Any) -> Any:
    if isinstance(response, Mapping):
        return response.get("usage")
    return getattr(response, "usage", None)


def _fallback_output_tokens(response: Any, gateway: Any = None) -> int:
    """Estimate visible output without probing a tokenizer for empty output."""

    text = _response_text(response)
    if not text:
        return 0
    measurement = _provider_text_measurement(
        text, _gateway_text_token_counter(gateway)
    )
    return _available_count(measurement)


def estimate_payload_tokens(
    payload: Any,
    response: Any = None,
    *,
    request_input_measurement: RequestInputMeasurement | None = None,
    gateway: Any = None,
) -> int:
    """Return fallback accounting for a legacy payload and visible output."""

    if normalize_usage(_response_usage(response))[4]:
        return 0
    measurement = request_input_measurement or measure_payload_input_tokens(payload)
    return _available_count(measurement) + _fallback_output_tokens(response, gateway)


def estimate_model_request_tokens(
    request: Any,
    response: Any = None,
    *,
    request_input_measurement: RequestInputMeasurement | None = None,
    gateway: Any = None,
    usage: Any = None,
) -> int:
    """Return fallback accounting using an already measured request when given.

    This helper intentionally returns an estimate, not exact usage. Provider
    call paths pass the pre-dispatch measurement so input is never recomputed
    from content after dispatch.
    """

    if usage is None:
        usage = _response_usage(response)
    if normalize_usage(usage)[4]:
        return 0
    measurement = request_input_measurement or measure_model_request_input_tokens(request)
    return _available_count(measurement) + _fallback_output_tokens(response, gateway)


def estimate_model_request_allowance(
    request: Any,
    gateway: Any = None,
    *,
    token_counter: Any = None,
    request_input_measurement: RequestInputMeasurement | None = None,
) -> int:
    """Return the hard preflight allowance for one model request."""

    measurement = request_input_measurement or measure_model_request_input_tokens(
        request, gateway, token_counter=token_counter
    )
    output = getattr(request, "max_output_tokens", 0)
    if isinstance(output, bool) or not isinstance(output, int):
        output = 0
    return max(1, _available_count(measurement) + max(0, output))


def estimate_payload_allowance(
    payload: Any,
    token_counter: Any = None,
    *,
    request_input_measurement: RequestInputMeasurement | None = None,
) -> int:
    """Return a compatibility allowance with explicit fallback semantics."""

    measurement = request_input_measurement or measure_payload_input_tokens(
        payload, token_counter
    )
    output = payload.get("max_output_tokens") if isinstance(payload, Mapping) else None
    if output is None and isinstance(payload, Mapping):
        output = payload.get("max_tokens", 0)
    if isinstance(output, bool) or not isinstance(output, int):
        output = 0
    return max(1, _available_count(measurement) + max(0, output))


__all__ = [
    "HEURISTIC_CHARS_PER_TOKEN",
    "PROVIDER_CHAT_INPUT_TOKENS",
    "PROVIDER_TEXT_TOKENIZER",
    "RequestInputMeasurement",
    "UNAVAILABLE",
    "estimate_model_request_allowance",
    "estimate_model_request_tokens",
    "estimate_payload_allowance",
    "estimate_payload_tokens",
    "measure_model_request_input_tokens",
    "measure_payload_input_tokens",
    "measure_request_input_tokens_from_texts",
]

"""Translation helpers for the pre-ModelRequest payload boundary."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Dict, Optional, cast

from agent.llm.contracts import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    StructuredOutputMode,
    StructuredOutputRequest,
    response_text,
    response_usage,
)
from agent.llm.decision_contract import ModelRequestContract, coerce_request_contract

_LEGACY_CANONICAL_FIELDS = frozenset(
    {
        "messages",
        "model",
        "temperature",
        "max_tokens",
        "max_output_tokens",
        "stream",
        "reasoning_budget",
        "grammar",
        "provider_options",
    }
)


def legacy_provider_options(payload: Mapping[str, Any]) -> Dict[str, Any]:
    """Keep provider-specific fields while removing only canonical fields."""

    options = {
        key: value
        for key, value in payload.items()
        if key not in _LEGACY_CANONICAL_FIELDS
    }
    nested = payload.get("provider_options")
    if isinstance(nested, Mapping):
        options.update(nested)
    return options


def _session_config(session: Any) -> Mapping[str, Any]:
    value = getattr(session, "config", {})
    return value if isinstance(value, Mapping) else {}


def _gateway_profile(session: Any) -> Mapping[str, Any]:
    value = getattr(getattr(session, "gateway", None), "profile", {})
    return value if isinstance(value, Mapping) else {}


def _integer(value: Any, default: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def build_legacy_model_request(
    session: Any,
    payload: Dict[str, Any],
    *,
    grammar: Optional[str] = None,
    request_contract: ModelRequestContract | str | None = None,
) -> ModelRequest:
    """Translate an old payload into the canonical request contract."""

    config = _session_config(session)
    profile = _gateway_profile(session)
    hardware_profile = getattr(session, "hardware_profile", None)
    effective_grammar = grammar
    if effective_grammar is None and isinstance(payload.get("grammar"), str):
        effective_grammar = cast(str, payload["grammar"])

    raw_messages = payload.get("messages")
    if isinstance(raw_messages, list):
        messages = tuple(
            ModelMessage(
                role=str(message.get("role", "user")),
                content=str(message.get("content", "")),
            )
            for message in raw_messages
            if isinstance(message, dict)
        )
    else:
        try:
            from agent.llm.session_requests import build_model_request

            messages = tuple(
                build_model_request(
                    session,
                    grammar=effective_grammar,
                    stream=False,
                    request_contract=request_contract,
                ).messages
            )
        except Exception:
            messages = ()
    structured = (
        StructuredOutputRequest(
            mode=StructuredOutputMode.GBNF,
            grammar=effective_grammar,
        )
        if effective_grammar is not None
        else None
    )
    configured_max_tokens = profile.get(
        "max_tokens",
        config.get("max_tokens", getattr(hardware_profile, "default_output_tokens", 1024)),
    )
    raw_max_tokens = payload.get(
        "max_tokens", payload.get("max_output_tokens", configured_max_tokens)
    )
    return ModelRequest(
        messages=messages,
        model=str(
            payload.get(
                "model", getattr(session.gateway, "model", config.get("model", "default"))
            )
        ),
        temperature=float(payload.get("temperature", config.get("temperature", 0.6))),
        max_output_tokens=_integer(raw_max_tokens, 1024),
        stream=bool(payload.get("stream", False)),
        reasoning_budget=_integer(
            payload.get("reasoning_budget", getattr(session, "thinking_budget", 0)),
            0,
        ),
        structured_output=structured,
        provider_options=legacy_provider_options(payload),
        context_limit=getattr(hardware_profile, "context_limit", None),
        request_contract=coerce_request_contract(request_contract),
    )


def legacy_payload(gateway: Any, request: ModelRequest) -> Dict[str, Any]:
    """Add fields expected by adapters that predate ``ModelRequest``."""

    builder = getattr(gateway, "build_payload", None)
    if callable(builder):
        built = builder(request)
        payload = dict(built) if isinstance(built, Mapping) else {}
    else:
        payload = {
            "model": request.model,
            "messages": [
                {"role": message.role, "content": message.content}
                for message in request.messages
            ],
            "temperature": request.temperature,
        }
    payload.update(request.provider_options)
    payload["max_tokens"] = request.max_output_tokens
    payload["stream"] = request.stream
    payload.pop("grammar", None)
    structured = request.structured_output
    if structured is not None and structured.grammar:
        payload["grammar"] = structured.grammar
    return payload


def legacy_payload_from_request(
    base_payload: Mapping[str, Any], request: ModelRequest
) -> Dict[str, Any]:
    """Apply a canonical request to an old payload without dropping options."""

    payload = dict(base_payload)
    if request.messages:
        payload["messages"] = [
            {"role": message.role, "content": message.content}
            for message in request.messages
        ]
    payload["model"] = request.model
    payload["temperature"] = request.temperature
    payload["max_tokens"] = request.max_output_tokens
    payload["stream"] = request.stream
    payload.pop("grammar", None)
    payload.update(request.provider_options)
    structured = request.structured_output
    if structured is not None and structured.grammar:
        payload["grammar"] = structured.grammar
    return payload


def complete_legacy_payload_request(
    session: Any,
    base_payload: Mapping[str, Any],
    request: ModelRequest,
) -> ModelResponse:
    """Use an old session completion method behind the canonical response type."""

    raw_response = session.send_non_streaming_request(
        legacy_payload_from_request(base_payload, request)
    )
    if isinstance(raw_response, ModelResponse):
        return raw_response
    usage = response_usage(raw_response)
    return ModelResponse(
        content=response_text(raw_response),
        usage=usage if usage is not None else ModelResponse(content="").usage,
    )


__all__ = [
    "build_legacy_model_request",
    "complete_legacy_payload_request",
    "legacy_payload",
    "legacy_payload_from_request",
    "legacy_provider_options",
]

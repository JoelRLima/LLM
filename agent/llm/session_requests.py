"""Canonical request and stream operations for ChatSession."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Dict, Optional, cast

from agent.llm.contracts import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    StructuredOutputMode,
    StructuredOutputRequest,
)
from agent.llm.decision_contract import ModelRequestContract, coerce_request_contract
from agent.llm.legacy_payload import (
    build_legacy_model_request,
    complete_legacy_payload_request,
    legacy_payload_from_request,
)
from agent.llm.model_profile import (
    ResolvedModelProfile,
    resolve_gateway_model_profile,
)


def _mapping_value(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _session_config(session: Any) -> Mapping[str, Any]:
    return _mapping_value(getattr(session, "config", {}))


def _session_profile(session: Any) -> ResolvedModelProfile:
    profile = getattr(session, "model_profile", None)
    if isinstance(profile, ResolvedModelProfile):
        return profile
    return resolve_gateway_model_profile(
        _session_config(session),
        getattr(session, "gateway", None),
    )


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
        None
        if grammar is None
        else StructuredOutputRequest(mode=StructuredOutputMode.GBNF, grammar=grammar)
    )
    profile = _session_profile(session)
    hardware_profile = getattr(session, "hardware_profile", None)
    configured_output_tokens = _integer(profile.max_output_tokens, 1024)
    return ModelRequest(
        messages=tuple(
            ModelMessage(role=message["role"], content=message["content"])
            for message in payload_messages
        ),
        model=profile.model,
        temperature=profile.temperature,
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
    """Delegate canonical completion to the shared model-call lifecycle."""

    from agent.runtime.model_call import ModelCallService

    return cast(
        ModelResponse,
        ModelCallService.for_session(session).complete(request).response,
    )


def consume_model_stream(
    session: Any,
    request: ModelRequest,
    callbacks: Dict[str, Callable[..., Any]],
) -> str:
    """Delegate canonical streaming to the shared model-call lifecycle."""

    from agent.runtime.model_call import ModelCallService

    return ModelCallService.for_session(session).stream(request, callbacks).text


__all__ = [
    "build_legacy_model_request",
    "build_model_request",
    "complete_legacy_payload_request",
    "complete_model_request",
    "consume_model_stream",
    "legacy_payload_from_request",
]

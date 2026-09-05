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


def resolve_effective_reasoning_budget(
    requested_reasoning_budget: int,
    max_output_tokens: int,
    reasoning_supported: bool,
) -> int:
    """Clamp a desired reasoning budget to the output geometry (W12 P20)."""

    if not reasoning_supported:
        return 0
    requested = max(0, requested_reasoning_budget)
    output = max_output_tokens
    if output <= 1 or requested == 0:
        return 0
    final_output_reserve = min(256, max(1, output // 4))
    max_safe_reasoning = max(0, output - final_output_reserve)
    return min(requested, max_safe_reasoning)


def build_effective_system_prompt_for_budget(
    base_system_prompt: str,
    effective_reasoning: int,
) -> str:
    """Use the canonical session wording with the transport-effective value."""

    if effective_reasoning <= 0:
        return base_system_prompt
    return (
        base_system_prompt
        + f"\n\n[THINKING]: You may spend up to {effective_reasoning} tokens thinking. "
        "This is a maximum limit, not a target. Stop as soon as you have a satisfactory answer. "
        "Be concise."
    )
def build_model_request(
    session: Any,
    response_format: Optional[str] = None,
    grammar: Optional[str] = None,
    *,
    stream: bool = True,
    max_output_tokens: int | None = None,
    request_contract: ModelRequestContract | str | None = None,
) -> ModelRequest:
    profile = _session_profile(session)
    configured_output_tokens = _integer(profile.max_output_tokens, 1024)
    requested_output_tokens = (
        configured_output_tokens if max_output_tokens is None else int(max_output_tokens)
    )
    output_tokens = max(1, requested_output_tokens)
    capabilities = getattr(profile, "capabilities", None) or getattr(
        getattr(session, "gateway", None), "capabilities", None
    )
    reasoning_supported = bool(getattr(capabilities, "reasoning", False))
    effective_reasoning = resolve_effective_reasoning_budget(
        _integer(getattr(session, "thinking_budget", 0), 0),
        output_tokens,
        reasoning_supported,
    )
    base_system_prompt = session.messages[0]["content"]
    system_content = build_effective_system_prompt_for_budget(
        base_system_prompt,
        effective_reasoning,
    )
    if response_format:
        system_content += "\n\n" + response_format
    payload_messages = [{"role": "system", "content": system_content}] + session.messages[1:]
    structured = (
        None
        if grammar is None
        else StructuredOutputRequest(mode=StructuredOutputMode.GBNF, grammar=grammar)
    )
    hardware_profile = getattr(session, "hardware_profile", None)
    return ModelRequest(
        messages=tuple(
            ModelMessage(role=message["role"], content=message["content"])
            for message in payload_messages
        ),
        model=profile.model,
        temperature=profile.temperature,
        max_output_tokens=(
            output_tokens
        ),
        stream=stream,
        reasoning_budget=effective_reasoning,
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

    return cast(str, ModelCallService.for_session(session).stream(request, callbacks).text)


__all__ = [
    "build_effective_system_prompt_for_budget",
    "build_model_request",
    "complete_model_request",
    "consume_model_stream",
    "resolve_effective_reasoning_budget",
]

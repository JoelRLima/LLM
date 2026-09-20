"""Canonical request and stream operations for ChatSession."""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
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
from agent.llm.request_geometry import (
    resolve_effective_request_geometry,
    resolve_ordinary_reasoning_budget,
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
        return cast(int, int(value))
    except (TypeError, ValueError):
        return default


def resolve_effective_reasoning_budget(
    requested_reasoning_budget: int,
    max_output_tokens: int,
    reasoning_supported: bool,
) -> int:
    """Backward-compatible W12 export for the ordinary clamp.

    The canonical owner retains the ``final_output_reserve`` and
    ``max_safe_reasoning`` calculations in ``request_geometry``; this
    compatibility seam deliberately delegates instead of duplicating them.
    """

    return cast(
        int,
        resolve_ordinary_reasoning_budget(
            requested_reasoning_budget,
            max_output_tokens,
            reasoning_supported,
        ),
    )


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
    structured = (
        None
        if grammar is None
        else StructuredOutputRequest(mode=StructuredOutputMode.GBNF, grammar=grammar)
    )
    geometry = resolve_effective_request_geometry(
        _integer(getattr(session, "thinking_budget", 0), 0),
        output_tokens,
        reasoning_supported,
        structured.mode if structured is not None else None,
        profile.compatibility,
        capabilities=capabilities,
    )
    base_system_prompt = session.messages[0]["content"]
    system_content = build_effective_system_prompt_for_budget(
        base_system_prompt,
        geometry.effective_reasoning_budget,
    )
    if response_format:
        system_content += "\n\n" + response_format
    payload_messages = [{"role": "system", "content": system_content}] + session.messages[1:]
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
        reasoning_budget=geometry.effective_reasoning_budget,
        requested_reasoning_budget=geometry.requested_reasoning_budget,
        compatibility_reason_code=geometry.compatibility_reason_code,
        structured_output=structured,
        context_limit=getattr(hardware_profile, "context_limit", None),
        request_contract=coerce_request_contract(request_contract),
    )


def build_ephemeral_model_request(
    session: Any,
    messages: Sequence[ModelMessage],
    *,
    stream: bool = False,
    max_output_tokens: int | None = None,
    request_contract: ModelRequestContract | str | None = None,
    structured_output: StructuredOutputRequest | None = None,
) -> ModelRequest:
    """Build a request from explicit messages without touching session history."""

    copied = tuple(messages)
    if not all(isinstance(message, ModelMessage) for message in copied):
        raise TypeError("messages must contain ModelMessage values")
    if not copied or not any(message.role == "system" for message in copied) or not any(message.role == "user" for message in copied):
        raise ValueError("ephemeral model request requires system and user messages")
    profile = _session_profile(session)
    configured_output_tokens = _integer(profile.max_output_tokens, 1024)
    requested_output_tokens = configured_output_tokens if max_output_tokens is None else int(max_output_tokens)
    output_tokens = max(1, requested_output_tokens)
    capabilities = getattr(profile, "capabilities", None) or getattr(
        getattr(session, "gateway", None), "capabilities", None
    )
    reasoning_supported = bool(getattr(capabilities, "reasoning", False))
    geometry = resolve_effective_request_geometry(
        _integer(getattr(session, "thinking_budget", 0), 0),
        output_tokens,
        reasoning_supported,
        structured_output.mode if structured_output is not None else None,
        profile.compatibility,
        capabilities=capabilities,
    )
    base_system_prompt = copied[0].content
    system_content = build_effective_system_prompt_for_budget(
        base_system_prompt,
        geometry.effective_reasoning_budget,
    )
    payload = (ModelMessage(role="system", content=system_content),) + copied[1:]
    hardware_profile = getattr(session, "hardware_profile", None)
    return ModelRequest(
        messages=payload,
        model=profile.model,
        temperature=profile.temperature,
        max_output_tokens=output_tokens,
        stream=stream,
        reasoning_budget=geometry.effective_reasoning_budget,
        requested_reasoning_budget=geometry.requested_reasoning_budget,
        compatibility_reason_code=geometry.compatibility_reason_code,
        structured_output=structured_output,
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
    "build_ephemeral_model_request",
    "build_model_request",
    "complete_model_request",
    "consume_model_stream",
    "resolve_effective_reasoning_budget",
]

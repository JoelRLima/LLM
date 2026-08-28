"""Deterministic token estimates used by the task budget boundary."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Callable


def estimate_payload_tokens(payload: Any, response: Any = None) -> int:
    texts: list[str] = []
    messages = payload.get("messages") if isinstance(payload, Mapping) else None
    if isinstance(messages, list):
        texts.extend(
            str(message.get("content", ""))
            for message in messages
            if isinstance(message, Mapping)
        )
    if isinstance(response, str):
        texts.append(response)
    elif isinstance(response, Mapping):
        content = response.get("content")
        if isinstance(content, str):
            texts.append(content)
    else:
        content = getattr(response, "content", None)
        if isinstance(content, str):
            texts.append(content)
    return sum(len(text) for text in texts) // 4


def estimate_model_request_tokens(request: Any, response: Any = None) -> int:
    messages = getattr(request, "messages", ())
    texts = [str(getattr(message, "content", "")) for message in messages]
    content = getattr(response, "content", None)
    if isinstance(content, str):
        texts.append(content)
    return sum(len(text) for text in texts) // 4


def _conservative_text_tokens(text: str) -> int:
    """Estimate text tokens conservatively without claiming provider usage."""

    if not text:
        return 0
    return max(1, (len(text) + 3) // 4)


def _provider_prompt_tokens(
    texts: list[str], token_counter: Callable[[str], Any] | None,
) -> int:
    return measure_request_input_tokens_from_texts(texts, token_counter)[0]


def measure_request_input_tokens_from_texts(
    texts: list[str], token_counter: Callable[[str], Any] | None,
) -> tuple[int, str]:
    """Measure final request input with explicit estimator provenance."""

    joined = "\n".join(texts)
    if token_counter is not None:
        try:
            value = token_counter(joined)
        except Exception as exc:
            from agent.runtime.budget import BudgetExhausted

            if isinstance(exc, BudgetExhausted):
                raise
            value = None
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return max(1, value) if joined else 0, "provider_token_counter"
    return _conservative_text_tokens(joined), "heuristic_chars_per_token"


def measure_model_request_input_tokens(
    request: Any, token_counter: Callable[[str], Any] | None = None,
) -> tuple[int, str]:
    """Measure the input portion of the exact ``ModelRequest`` about to dispatch."""

    messages = getattr(request, "messages", ())
    texts = [str(getattr(message, "content", "")) for message in messages]
    return measure_request_input_tokens_from_texts(texts, token_counter)


def estimate_model_request_allowance(
    request: Any, token_counter: Callable[[str], Any] | None = None,
) -> int:
    """Return the hard preflight allowance for one model request."""

    input_tokens, _ = measure_model_request_input_tokens(request, token_counter)
    output = getattr(request, "max_output_tokens", 0)
    if isinstance(output, bool) or not isinstance(output, int):
        output = 0
    return max(1, input_tokens + max(0, output))


def estimate_payload_allowance(
    payload: Any, token_counter: Callable[[str], Any] | None = None,
) -> int:
    """Return the equivalent hard allowance for a legacy payload."""

    messages = payload.get("messages") if isinstance(payload, Mapping) else None
    texts = [
        str(message.get("content", ""))
        for message in messages or ()
        if isinstance(message, Mapping)
    ]
    output = payload.get("max_output_tokens") if isinstance(payload, Mapping) else None
    if output is None and isinstance(payload, Mapping):
        output = payload.get("max_tokens", 0)
    if isinstance(output, bool) or not isinstance(output, int):
        output = 0
    return max(1, _provider_prompt_tokens(texts, token_counter) + max(0, output))


__all__ = [
    "estimate_model_request_allowance",
    "estimate_model_request_tokens",
    "measure_model_request_input_tokens",
    "estimate_payload_allowance",
    "estimate_payload_tokens",
]

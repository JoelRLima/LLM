"""OpenAI-compatible request-input counter implementation."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

import requests

from agent.llm.contracts import ModelRequest
from agent.runtime.budget_estimation import (
    HEURISTIC_CHARS_PER_TOKEN,
    PROVIDER_CHAT_INPUT_TOKENS,
    PROVIDER_TEXT_TOKENIZER,
    RequestInputMeasurement,
)


def extension_url(api_url: str, path: str) -> str:
    if path.startswith(("http://", "https://")):
        return path
    parsed = urlsplit(api_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    prefix = parsed.path.split("/v1/", 1)[0] if "/v1/" in parsed.path else ""
    normalized = path if path.startswith("/") else f"/{path}"
    return f"{origin}{prefix}{normalized}"


def _request_text(request: ModelRequest) -> str:
    return "\n".join(str(message.content) for message in request.messages)


def _count_request_input_tokens(gateway: Any, request: ModelRequest) -> int | None:
    capabilities = getattr(gateway, "capabilities", None)
    if not getattr(capabilities, "token_counting", False):
        return None
    if getattr(gateway, "_request_input_tokens_supported", None) is False:
        return None
    path = str(
        gateway.provider_options.get(
            "input_tokens_path", "/v1/chat/completions/input_tokens"
        )
    )
    try:
        # build_payload is the same owner used by complete() and stream().
        payload = gateway.build_payload(request)
        response = requests.post(
            extension_url(gateway.api_url, path),
            json=payload,
            timeout=min(gateway.timeout, 10),
        )
        if response.status_code in (404, 405):
            gateway._request_input_tokens_supported = False
            return None
        if response.status_code != 200:
            return None
        data = response.json()
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    value = data.get("input_tokens")
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    gateway._request_input_tokens_supported = True
    return value


def measure_request_input_tokens(
    gateway: Any, request: ModelRequest
) -> RequestInputMeasurement:
    """Count the canonical chat request, then use explicit lower-fidelity fallbacks."""

    exact = _count_request_input_tokens(gateway, request)
    if exact is not None:
        return RequestInputMeasurement(
            exact, PROVIDER_CHAT_INPUT_TOKENS, exact=True, available=True
        )
    text = _request_text(request)
    try:
        text_count = gateway.count_tokens(text)
    except Exception:
        text_count = None
    if isinstance(text_count, int) and not isinstance(text_count, bool) and text_count >= 0:
        return RequestInputMeasurement(
            text_count, PROVIDER_TEXT_TOKENIZER, exact=False, available=True
        )
    return RequestInputMeasurement(
        max(1, (len(text) + 3) // 4) if text else 0,
        HEURISTIC_CHARS_PER_TOKEN,
        exact=False,
        available=True,
    )


__all__ = ["extension_url", "measure_request_input_tokens"]

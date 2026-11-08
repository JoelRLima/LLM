"""OpenAI-compatible request-input counter implementation."""

from __future__ import annotations

import hashlib
import json
from contextlib import closing
from typing import Any
from urllib.parse import urlsplit

import requests

from llm_agent.agent.llm.contracts import ModelRequest
from llm_agent.agent.llm.errors import UnsupportedModelCapability
from llm_agent.agent.runtime.budget_estimation import (
    PROVIDER_CHAT_INPUT_TOKENS,
    RequestInputMeasurement,
)


def request_measurement_fingerprint(gateway: Any, request: Any) -> str | None:
    """Hash effective wire geometry; never retain or expose prompt contents."""
    if not isinstance(request, ModelRequest) or not gateway.capabilities.token_counting:
        return None
    try:
        payload = {
            "request": gateway.build_payload(request),
            "endpoint": gateway.api_url,
            "profile": gateway.resolved_profile.fingerprint,
            "capabilities": gateway.capabilities.to_dict(),
            "provider_options": gateway.provider_options,
            "credential_reference": repr(gateway.credential_ref),
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    except (AttributeError, TypeError, ValueError, RecursionError, UnsupportedModelCapability):
        return None


def extension_url(api_url: str, path: str) -> str:
    if path.startswith(("http://", "https://")):
        return path
    parsed = urlsplit(api_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    prefix = parsed.path.split("/v1/", 1)[0] if "/v1/" in parsed.path else ""
    normalized = path if path.startswith("/") else f"/{path}"
    return f"{origin}{prefix}{normalized}"


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
        request_kwargs: dict[str, Any] = {
            "json": payload,
            "timeout": min(gateway.timeout, 10),
        }
        headers = gateway._request_headers()
        if headers is not None:
            request_kwargs["headers"] = headers
        response = requests.post(extension_url(gateway.api_url, path), **request_kwargs)
        with closing(response):
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
    """Return only exact provider chat-request usage when the endpoint supports it.

    The neutral runtime measurement owner applies text-tokenizer and heuristic
    fallbacks after this provider-specific boundary reports unavailability.
    """

    exact = _count_request_input_tokens(gateway, request)
    if exact is not None:
        return RequestInputMeasurement(
            exact, PROVIDER_CHAT_INPUT_TOKENS, exact=True, available=True
        )
    return RequestInputMeasurement.unavailable()


__all__ = ["extension_url", "measure_request_input_tokens"]

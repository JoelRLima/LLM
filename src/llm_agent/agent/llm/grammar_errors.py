"""Recognize explicit grammar capability rejection without hiding syntax errors."""
from __future__ import annotations

import json
import re
from typing import Any

_UNSUPPORTED = tuple(re.compile(pattern, re.IGNORECASE) for pattern in (
    r"\b(?:grammar|gbnf)\b\s+(?:(?:field|parameter|argument|option)\s+)?(?:is\s+)?(?:not\s+supported|unsupported|not\s+implemented|disabled)\b",
    r"\b(?:unsupported|unknown|unrecognized|unexpected)\s+(?:(?:(?:request|keyword)\s+)?(?:field|parameter|argument|option|key)(?:\s+supplied)?\s*[:=]?\s*)?[`'\" ]*(?:grammar|gbnf)\b(?!\s+(?:syntax|rule|construct|expression|token|definition|root|character|escape)\b)",
    r"\b(?:does\s+not|doesn't)\s+support\s+(?:the\s+)?(?:grammar|gbnf)\b",
))


def _error_message(text: str) -> str | None:
    """Ignore echoed requests and unrelated JSON fields when a message exists."""
    try:
        value: Any = json.loads(text)
    except (ValueError, RecursionError):
        return text
    if not isinstance(value, dict):
        return None
    error = value.get("error", value)
    if isinstance(error, str):
        return error
    if not isinstance(error, dict):
        return None
    message = error.get("message", error.get("detail"))
    return message if isinstance(message, str) else None


def is_grammar_unsupported_error(error: Exception) -> bool:
    """Only an explicit HTTP 400 capability rejection permits degradation."""
    response = getattr(error, "response", None)
    if response is None or getattr(response, "status_code", None) != 400:
        return False
    try:
        body = response.text
    except Exception:
        return False
    if not isinstance(body, str):
        return False
    message = _error_message(body)
    return bool(message and any(pattern.search(message) for pattern in _UNSUPPORTED))


__all__ = ["is_grammar_unsupported_error"]

"""Small, shared redaction boundary for persisted public projections."""

from __future__ import annotations

import re
from typing import Any

_SENSITIVE_PATTERNS = (
    (
        re.compile(r"(?i)(authorization\s*:\s*bearer\s+)[^\s,;]+"),
        r"\1[REDACTED]",
    ),
    (
        re.compile(
            r"(?i)(\b(?:api[_-]?key|token|password|secret)\s*[:=]\s*)[^\s,;]+"
        ),
        r"\1[REDACTED]",
    ),
    (
        re.compile(r"(?i)([?&](?:api[_-]?key|token|password)=)[^&\s]+"),
        r"\1[REDACTED]",
    ),
)


def sanitize_public_text(value: Any) -> str:
    """Redact common credential forms before text enters public artifacts."""

    text = "" if value is None else str(value)
    for pattern, replacement in _SENSITIVE_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


__all__ = ["sanitize_public_text"]

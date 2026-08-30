"""Bounded, deterministic values used by canonical runtime events."""

from __future__ import annotations

import json
import re
from types import MappingProxyType
from typing import Any, Mapping

MAX_EVENT_DEPTH = 4
MAX_EVENT_ITEMS = 32
MAX_EVENT_TEXT = 512
MAX_EVENT_DATA_CHARS = 8192
RESERVED_EVENT_IDENTITY_FIELDS = frozenset(
    {
        "run_id",
        "root_task_id",
        "task_id",
        "parent_task_id",
        "node_id",
        "plan_id",
        "step_id",
        "invocation_id",
    }
)
_EVENT_SECRET_KEY_PATTERN = (
    r"(?:[A-Za-z][A-Za-z0-9-]*[_-])?"
    r"(?:api[_-]?key|token|password|secret)"
)
_EVENT_AUTHORIZATION_QUOTED_PATTERN = re.compile(
    r'''(?i)(\bauthorization\b\s*["']?\s*[:=]\s*)'''
    r'''(?P<quote>["'])(?P<credentials>(?:\\.|(?!(?P=quote))[\s\S])*)(?P=quote)'''
)
_EVENT_AUTHORIZATION_TEXT_PATTERN = re.compile(
    r'''(?i)(\bauthorization\b\s*["']?\s*[:=]\s*)'''
    r'''(?!["'])'''
    r'''(?:(?P<scheme>[A-Za-z][A-Za-z0-9_-]*)[^\S\r\n]+)?'''
    r'''(?P<credentials>[^\r\n]*)'''
)
_EVENT_SECRET_PATTERNS = (
    (
        re.compile(
            r'''(?i)(\b'''
            + _EVENT_SECRET_KEY_PATTERN
            + r'''\b\s*["']?\s*[:=]\s*)(?P<quote>["'])(.*?)(?P=quote)'''
        ),
        r'\1\g<quote>[REDACTED]\g<quote>',
    ),
    (
        re.compile(
            r'''(?i)(\b'''
            + _EVENT_SECRET_KEY_PATTERN
            + r'''\b\s*["']?\s*[:=]\s*)[^\s,;}\]"']+'''
        ),
        r'\1[REDACTED]',
    ),
    (
        re.compile(r'''(?i)(\bbearer\s+)(?P<quote>["'])(.*?)(?P=quote)'''),
        r'\1\g<quote>[REDACTED]\g<quote>',
    ),
    (
        re.compile(r'''(?i)(\bbearer\s+)(?!\[REDACTED\])[^\s,;}\]"']+'''),
        r'\1[REDACTED]',
    ),
)
_SENSITIVE_EVENT_KEYS = frozenset(
    {'authorization', 'apikey', 'token', 'password', 'secret'}
)
_SAFE_TOKEN_DIAGNOSTIC_KEYS = frozenset(
    {'tokenusage', 'tokenusagecomplete', 'tokencount'}
)


def _sanitize_event_text(value: str) -> str:
    sanitized = _EVENT_AUTHORIZATION_QUOTED_PATTERN.sub(
        _sanitize_authorization_match,
        value,
    )
    sanitized = _EVENT_AUTHORIZATION_TEXT_PATTERN.sub(
        _sanitize_authorization_match,
        sanitized,
    )
    for pattern, replacement in _EVENT_SECRET_PATTERNS:
        sanitized = pattern.sub(replacement, sanitized)
    return sanitized


def _sanitize_authorization_match(match: re.Match[str]) -> str:
    scheme = match.groupdict().get("scheme")
    if scheme is None:
        credentials = match.group("credentials")
        scheme_match = re.match(
            r"[A-Za-z][A-Za-z0-9_-]*(?=[^\S\r\n])",
            credentials,
        )
        scheme = scheme_match.group(0) if scheme_match else None
    scheme_text = f"{scheme} " if scheme else ""
    quote = match.groupdict().get("quote")
    if quote is None:
        return f"{match.group(1)}{scheme_text}[REDACTED]"
    return f"{match.group(1)}{quote}{scheme_text}[REDACTED]{quote}"


def _normalized_event_key(value: Any) -> str:
    return ''.join(character for character in str(value).casefold() if character.isalnum())


def _is_sensitive_event_key(value: Any) -> bool:
    normalized = _normalized_event_key(value)
    if normalized in _SAFE_TOKEN_DIAGNOSTIC_KEYS:
        return False
    if normalized in _SENSITIVE_EVENT_KEYS:
        return True
    return (
        normalized.startswith(('authorization', 'apikey', 'password', 'secret', 'token'))
        or normalized.endswith(('authorization', 'apikey', 'password', 'secret', 'token'))
    )


def _bounded_text(value: Any, *, limit: int = MAX_EVENT_TEXT) -> str:
    text = value if isinstance(value, str) else str(value)
    return text if len(text) <= limit else text[:limit] + "..."


def _bounded_value(value: Any, depth: int) -> Any:
    if depth > MAX_EVENT_DEPTH:
        return "<event-data-depth-truncated>"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _bounded_text(_sanitize_event_text(value))
    if isinstance(value, Mapping):
        projected: dict[str, Any] = {}
        items = list(value.items())
        for key, item in items[:MAX_EVENT_ITEMS]:
            projected_key = _bounded_text(_sanitize_event_text(str(key)), limit=128)
            projected[projected_key] = (
                '[REDACTED]'
                if _is_sensitive_event_key(key)
                else _bounded_value(item, depth + 1)
            )
        if len(items) > MAX_EVENT_ITEMS:
            projected["__truncated_items__"] = len(items) - MAX_EVENT_ITEMS
        return projected
    if isinstance(value, (list, tuple, set, frozenset)):
        items = sorted(value, key=repr) if isinstance(value, (set, frozenset)) else list(value)
        projected_items = [_bounded_value(item, depth + 1) for item in items[:MAX_EVENT_ITEMS]]
        if len(items) > MAX_EVENT_ITEMS:
            projected_items.append({"__truncated_items__": len(items) - MAX_EVENT_ITEMS})
        return projected_items
    return f"<unsupported:{type(value).__name__}>"


def bounded_event_data(data: Mapping[str, Any] | None) -> Mapping[str, Any]:
    """Project event diagnostics into a bounded, deterministic, secret-safe map."""

    raw = data if isinstance(data, Mapping) else {}
    projected = _bounded_value(raw, 0)
    if not isinstance(projected, dict):
        projected = {"value": projected}
    try:
        encoded = json.dumps(projected, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    except (TypeError, ValueError):
        encoded = "{}"
    if len(encoded) > MAX_EVENT_DATA_CHARS:
        projected = {
            "__truncated__": True,
            "preview": _bounded_text(encoded, limit=MAX_EVENT_DATA_CHARS - 64),
        }
    return MappingProxyType(projected)


def freeze_event_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): freeze_event_value(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(freeze_event_value(item) for item in value)
    return value


def unfreeze_event_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): unfreeze_event_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [unfreeze_event_value(item) for item in value]
    return value


def optional_event_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def event_id(value: Any, name: str, *, required: bool = False) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


__all__ = [
    "MAX_EVENT_DATA_CHARS",
    "MAX_EVENT_DEPTH",
    "MAX_EVENT_ITEMS",
    "MAX_EVENT_TEXT",
    "RESERVED_EVENT_IDENTITY_FIELDS",
    "bounded_event_data",
    "event_id",
    "freeze_event_value",
    "optional_event_string",
    "unfreeze_event_value",
]

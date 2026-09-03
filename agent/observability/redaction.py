"""Canonical bounded redaction for durable observation data."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

from agent.observability.redaction_keys import key_as_text

REDACTION_POLICY_VERSION = "w9-redaction-v1"
REDACTED_VALUE = "[REDACTED]"
OMITTED_VALUE = "[OMITTED]"

MAX_OBSERVATION_DEPTH = 4
MAX_OBSERVATION_ITEMS = 32
MAX_OBSERVATION_TEXT = 512
MAX_OBSERVATION_DATA_CHARS = 8192

_SAFE_METRIC_KEY_NAMES = frozenset(
    {
        "tokencount",
        "tokenusage",
        "tokenusagecomplete",
        "tokenbudget",
        "requesttokens",
        "responsetokens",
        "inputtokens",
        "outputtokens",
        "totaltokens",
    }
)
_SENSITIVE_KEY_NAMES = frozenset(
    {
        "api_key",
        "apikey",
        "apitoken",
        "access_token",
        "accesstoken",
        "refresh_token",
        "refreshtoken",
        "authorization",
        "cookie",
        "cookies",
        "session",
        "session_secret",
        "sessionsecret",
        "password",
        "passphrase",
        "secret",
        "credential",
        "credentials",
        "task_authority",
        "taskauthority",
        "authority_token",
        "authoritytoken",
        "capability_token",
        "capabilitytoken",
        "private_key",
        "privatekey",
        "set_cookie",
        "setcookie",
    }
)
_OMITTED_KEY_NAMES = frozenset(
    {
        "chain_of_thought",
        "chainofthought",
        "hidden_reasoning",
        "hiddenreasoning",
        "internal_reasoning",
        "internalreasoning",
        "reasoning_trace",
        "reasoningtrace",
        "raw_prompt",
        "rawprompt",
        "raw_completion",
        "rawcompletion",
        "raw_response",
        "rawresponse",
        "prompt",
        "completion",
        "environment",
        "environment_variables",
        "environmentvariables",
        "env",
        "workspace_contents",
        "workspacecontents",
    }
)

_BEARER_RE = re.compile(
    r"(?i)(\bbearer\s+)(?!\[REDACTED\])([^\s,;\]}\"']+)"
)
_ASSIGNMENT_RE = re.compile(
    r"(?i)(\b(?:api[ _-]?key|api[ _-]?token|access[ _-]?token|refresh[ _-]?token|"
    r"authorization|cookie|session[ _-]?secret|password|passphrase|secret|credentials?|"
    r"task[ _-]?authority(?:[ _-]?token)?|(?:capability|authority)[ _-]?token)\b"
    r"\s*[:=]\s*)(?P<quote>[\"']?)(?P<value>[^\s,;}\]\"']+)(?P=quote)"
)
_AUTHORIZATION_RE = re.compile(
    r"(?i)(\bauthorization\s*[:=]\s*)(?P<scheme>[A-Za-z][A-Za-z0-9_-]*\s+)?"
    r"(?P<quote>[\"']?)(?P<value>[^\s,;}\]\"']+)(?P=quote)"
)


def _normalized_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]", "", key.casefold())


_NORMALIZED_SENSITIVE_KEYS = frozenset(_normalized_key(item) for item in _SENSITIVE_KEY_NAMES)
_NORMALIZED_OMITTED_KEYS = frozenset(_normalized_key(item) for item in _OMITTED_KEY_NAMES)


def _is_sensitive_key(key: str) -> bool:
    normalized = _normalized_key(key)
    if normalized in _SAFE_METRIC_KEY_NAMES:
        return False
    if normalized in _NORMALIZED_SENSITIVE_KEYS:
        return True
    return normalized.startswith(
        (
            "apikey",
            "apitoken",
            "accesstoken",
            "refreshtoken",
            "authorization",
            "password",
            "passphrase",
            "secret",
            "credential",
            "cookie",
            "taskauthoritytoken",
            "authoritytoken",
            "capabilitytoken",
        )
    ) or normalized.endswith(
        (
            "apikey",
            "apitoken",
            "accesstoken",
            "refreshtoken",
            "authorization",
            "password",
            "passphrase",
            "secret",
            "credential",
            "cookie",
            "authoritytoken",
            "capabilitytoken",
        )
    )


def _is_omitted_key(key: str) -> bool:
    return _normalized_key(key) in _NORMALIZED_OMITTED_KEYS


def redact_text(value: str, *, limit: int = MAX_OBSERVATION_TEXT) -> str:
    """Redact credential-like text before applying its display bound."""

    if not isinstance(value, str):
        raise TypeError("observation text must be a string")

    def assignment(match: re.Match[str]) -> str:
        quote = match.group("quote")
        return f"{match.group(1)}{quote}{REDACTED_VALUE}{quote}"

    sanitized = _AUTHORIZATION_RE.sub(assignment, value)
    sanitized = _ASSIGNMENT_RE.sub(assignment, sanitized)
    sanitized = _BEARER_RE.sub(rf"\1{REDACTED_VALUE}", sanitized)
    if len(sanitized) <= limit:
        return sanitized
    return sanitized[: max(0, limit - 3)] + "..."


def _redact_mapping(value: Mapping[Any, Any], depth: int) -> dict[str, Any]:
    items: list[tuple[str, Any]] = []
    for raw_key, raw_item in value.items():
        key = redact_text(key_as_text(raw_key), limit=128)
        if _is_omitted_key(key):
            item = OMITTED_VALUE
        elif _is_sensitive_key(key):
            item = REDACTED_VALUE
        else:
            item = _redact(raw_item, depth + 1)
        items.append((key, item))
    items.sort(key=lambda item: item[0])
    projected: dict[str, Any] = dict(items[:MAX_OBSERVATION_ITEMS])
    if len(items) > MAX_OBSERVATION_ITEMS:
        projected["__truncated_items__"] = len(items) - MAX_OBSERVATION_ITEMS
    return projected


def _redact_sequence(value: list[Any] | tuple[Any, ...], depth: int) -> list[Any]:
    projected = [_redact(item, depth + 1) for item in value[:MAX_OBSERVATION_ITEMS]]
    if len(value) > MAX_OBSERVATION_ITEMS:
        projected.append({"__truncated_items__": len(value) - MAX_OBSERVATION_ITEMS})
    return projected


def _redact(value: Any, depth: int) -> Any:
    if depth > MAX_OBSERVATION_DEPTH:
        return "<observation-depth-truncated>"
    if value is None or isinstance(value, bool) or isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise TypeError("non-finite observation numbers are not supported")
        return value
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Mapping):
        return _redact_mapping(value, depth)
    if isinstance(value, (list, tuple)):
        return _redact_sequence(value, depth)
    raise TypeError(
        f"unsupported observation value type: {type(value).__name__}; normalize it explicitly"
    )


def canonical_json(value: Any) -> str:
    """Serialize already-redacted JSON-compatible data deterministically."""

    return json.dumps(
        unfreeze_observation_value(value),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def freeze_observation_value(value: Any) -> Any:
    """Freeze a redacted JSON value for immutable public records."""

    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): freeze_observation_value(item) for key, item in value.items()}
        )
    if isinstance(value, list):
        return tuple(freeze_observation_value(item) for item in value)
    if isinstance(value, tuple):
        return tuple(freeze_observation_value(item) for item in value)
    return value


def unfreeze_observation_value(value: Any) -> Any:
    """Return a JSON-compatible copy of a frozen redacted value."""

    if isinstance(value, Mapping):
        return {str(key): unfreeze_observation_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [unfreeze_observation_value(item) for item in value]
    return value


def redact_observation_value(
    value: Any,
    *,
    max_chars: int = MAX_OBSERVATION_DATA_CHARS,
) -> Any:
    """Return a bounded, JSON-compatible, recursively redacted projection."""

    if isinstance(max_chars, bool) or not isinstance(max_chars, int) or max_chars < 35:
        raise ValueError("max_chars must be an integer of at least 35")
    projected = _redact(value, 0)
    encoded = canonical_json(projected)
    if len(encoded) <= max_chars:
        return projected
    marker = "<observation-data-truncated>"
    compact = {"__truncated__": True, "preview": encoded[: max(0, max_chars - 64)] + marker}
    while len(canonical_json(compact)) > max_chars and compact["preview"]:
        preview = str(compact["preview"])
        compact["preview"] = preview[: max(0, len(preview) - max(1, len(preview) // 8))]
    return compact


__all__ = [
    "MAX_OBSERVATION_DATA_CHARS",
    "MAX_OBSERVATION_DEPTH",
    "MAX_OBSERVATION_ITEMS",
    "MAX_OBSERVATION_TEXT",
    "OMITTED_VALUE",
    "REDACTED_VALUE",
    "REDACTION_POLICY_VERSION",
    "canonical_json",
    "freeze_observation_value",
    "redact_observation_value",
    "redact_text",
    "unfreeze_observation_value",
]

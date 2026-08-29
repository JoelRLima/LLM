"""Bounded, redacted invocation arguments retained for deterministic evaluation."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from agent.reporting.public_safety import sanitize_public_text

MAX_EVALUATION_ARGS_CHARS = 8_192
MAX_EVALUATION_ARG_ITEMS = 16
MAX_EVALUATION_ARG_DEPTH = 4
MAX_EVALUATION_ARG_TEXT = 256
_EVALUATION_ARG_PRIORITY = (
    "pattern",
    "query",
    "command",
    "file_path",
    "path",
    "target",
    "targets",
    "mode",
    "action",
    "recursive",
    "max_results",
    "bindings",
    "objective",
    "content",
    "replacement",
    "old_text",
    "new_text",
)
_SENSITIVE_ARGUMENT_KEYS = frozenset(
    {
        "access_token",
        "api_key",
        "apikey",
        "authorization",
        "password",
        "secret",
        "token",
    }
)


def _safe_value(
    value: Any,
    *,
    key: str | None = None,
    depth: int = 0,
    item_limit: int = MAX_EVALUATION_ARG_ITEMS,
    text_limit: int = MAX_EVALUATION_ARG_TEXT,
) -> Any:
    normalized_key = key.casefold().replace("-", "_") if key is not None else ""
    if normalized_key in _SENSITIVE_ARGUMENT_KEYS:
        return "[REDACTED]"
    if depth > MAX_EVALUATION_ARG_DEPTH:
        return "<evaluation-args-depth-truncated>"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        safe = str(sanitize_public_text(value))
        return safe[:text_limit] + ("..." if len(safe) > text_limit else "")
    if isinstance(value, Mapping):
        items = sorted(
            ((str(raw_key), raw_value) for raw_key, raw_value in value.items()),
            key=lambda item: item[0],
        )
        projected = {
            raw_key: _safe_value(
                raw_value,
                key=raw_key,
                depth=depth + 1,
                item_limit=item_limit,
                text_limit=text_limit,
            )
            for raw_key, raw_value in items[:item_limit]
        }
        if len(items) > item_limit:
            projected["__truncated_items__"] = len(items) - item_limit
        return projected
    if isinstance(value, (list, tuple, set, frozenset)):
        items = sorted(value, key=repr) if isinstance(value, (set, frozenset)) else list(value)
        projected_items = [
            _safe_value(
                item,
                depth=depth + 1,
                item_limit=item_limit,
                text_limit=text_limit,
            )
            for item in items[:item_limit]
        ]
        if len(items) > item_limit:
            projected_items.append({"__truncated_items__": len(items) - item_limit})
        return projected_items
    return f"<unsupported:{type(value).__name__}>"


def _json_size(value: Mapping[str, Any]) -> int:
    try:
        return len(json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True))
    except (TypeError, ValueError):
        return MAX_EVALUATION_ARGS_CHARS + 1


def project_evaluation_args(raw: Any) -> dict[str, Any]:
    """Preserve evaluator bindings in a deterministic, bounded public shape."""

    if not isinstance(raw, Mapping):
        return {}
    values = {str(key): value for key, value in raw.items()}
    priority = [key for key in _EVALUATION_ARG_PRIORITY if key in values]
    ordered_keys = priority + sorted(key for key in values if key not in priority)
    projected = {
        key: _safe_value(values[key], key=key) for key in ordered_keys[:32]
    }
    if _json_size(projected) <= MAX_EVALUATION_ARGS_CHARS:
        return projected

    critical = set(priority)
    for key in reversed(ordered_keys):
        if key in critical:
            continue
        projected.pop(key, None)
        if _json_size(projected) <= MAX_EVALUATION_ARGS_CHARS:
            return projected

    compact = {
        key: _safe_value(values[key], key=key, item_limit=8, text_limit=96)
        for key in priority
        if key in values
    }
    if _json_size(compact) <= MAX_EVALUATION_ARGS_CHARS:
        return compact
    return {
        key: value
        for key, value in compact.items()
        if _json_size({key: value}) <= MAX_EVALUATION_ARGS_CHARS
    }


__all__ = ["project_evaluation_args"]

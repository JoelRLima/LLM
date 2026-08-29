"""Bounded, deterministic values used by canonical runtime events."""

from __future__ import annotations

import json
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


def _bounded_text(value: Any, *, limit: int = MAX_EVENT_TEXT) -> str:
    text = value if isinstance(value, str) else str(value)
    return text if len(text) <= limit else text[:limit] + "..."


def _bounded_value(value: Any, depth: int) -> Any:
    if depth > MAX_EVENT_DEPTH:
        return "<event-data-depth-truncated>"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _bounded_text(value)
    if isinstance(value, Mapping):
        projected: dict[str, Any] = {}
        items = list(value.items())
        for key, item in items[:MAX_EVENT_ITEMS]:
            projected[_bounded_text(key, limit=128)] = _bounded_value(item, depth + 1)
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
    """Project event diagnostics into a deterministic JSON-safe bounded map."""

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

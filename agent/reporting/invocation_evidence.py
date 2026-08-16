"""Bounded, descriptor-owned projection of one executed invocation."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from .observation_evidence import PUBLIC_TOOL_STATUSES

MAX_INVOCATION_ARGS_CHARS = 1_000


def _json_default(value: Any) -> str:
    return str(value)[:2_000]


def _json_text(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=_json_default)
    except (TypeError, ValueError, OverflowError):
        return json.dumps(str(value), ensure_ascii=False, separators=(",", ":"))


def _safe_identity(value: Any, limit: int) -> str:
    return re.sub(r"[^A-Za-z0-9_.:@/+-]", "?", str(value))[:limit]


def _descriptor_for(lookup: Any, tool_name: str) -> Any:
    if lookup is None:
        return None
    if callable(lookup):
        try:
            return lookup(tool_name)
        except (KeyError, TypeError, AttributeError):
            return None
    resolver = getattr(lookup, "descriptor", None)
    if callable(resolver):
        try:
            return resolver(tool_name)
        except (KeyError, TypeError, AttributeError):
            return None
    return lookup.get(tool_name) if isinstance(lookup, Mapping) else None


def _bounded_args(args: Mapping[str, Any], fields: tuple[str, ...], max_chars: int) -> tuple[dict[str, Any], bool]:
    projected = {field: args[field] for field in fields if field in args}
    if len(_json_text({"args": projected})) <= max(2, max_chars):
        return projected, False
    clipped: dict[str, Any] = {}
    limit = max(32, max_chars - 32)
    for field in fields:
        if field not in args:
            continue
        candidate = {**clipped, field: args[field]}
        if len(_json_text({"args": candidate})) <= limit:
            clipped[field] = args[field]
            continue
        if not isinstance(args[field], str):
            continue
        marker = "...<truncated>"
        low, high, best = 0, len(args[field]), marker
        while low <= high:
            middle = (low + high) // 2
            trial = {**clipped, field: args[field][:middle] + marker}
            if len(_json_text({"args": trial})) <= limit:
                best, low = args[field][:middle] + marker, middle + 1
            else:
                high = middle - 1
        clipped[field] = best
    return clipped, True


def project_executed_invocation(
    entry: Mapping[str, Any],
    descriptor_lookup: Any = None,
    *,
    max_chars: int = MAX_INVOCATION_ARGS_CHARS,
) -> dict[str, Any]:
    """Expose only descriptor-approved arguments and canonical result flags."""
    raw_result = entry.get("result")
    result = raw_result if isinstance(raw_result, Mapping) else {}
    tool = str(entry.get("tool") or "")
    descriptor = _descriptor_for(descriptor_lookup, tool)
    fields = getattr(descriptor, "public_invocation_fields", ()) if descriptor is not None else ()
    fields = tuple(sorted(field for field in fields if isinstance(field, str)))
    raw_args = entry.get("args")
    args = raw_args if isinstance(raw_args, Mapping) else {}
    projected, truncated = _bounded_args(args, fields, max_chars)
    projection_complete = not truncated and set(str(key) for key in args) <= set(fields)
    invocation_id = entry.get("invocation_id") or result.get("invocation_id")
    raw_status = result.get("status") or entry.get("status")
    status = raw_status if isinstance(raw_status, str) and raw_status in PUBLIC_TOOL_STATUSES else "unknown"
    invocation: dict[str, Any] = {
        "values": projected,
        "projection_complete": projection_complete,
        "truncated": truncated,
        "status": status,
    }
    if invocation_id is not None:
        invocation["invocation_id"] = _safe_identity(invocation_id, 128)
    invocation["executed"] = result.get("executed") if type(result.get("executed")) is bool else None
    return invocation


__all__ = ["MAX_INVOCATION_ARGS_CHARS", "project_executed_invocation"]

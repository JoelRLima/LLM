"""Primitive readers used by the canonical metrics projection."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def first_number(entry: Mapping[str, Any], keys: tuple[str, ...]) -> int:
    for key in keys:
        value = entry.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return int(value)
    return 0


def has_number(entry: Mapping[str, Any], keys: tuple[str, ...]) -> bool:
    return any(
        isinstance(entry.get(key), (int, float)) and not isinstance(entry.get(key), bool)
        for key in keys
    )


def entry_usage_complete(entry: Mapping[str, Any]) -> bool:
    explicit = entry.get("token_usage_complete")
    if isinstance(explicit, bool):
        return explicit
    if has_number(entry, ("input_tokens", "prompt_tokens")) and has_number(
        entry, ("output_tokens", "completion_tokens")
    ):
        return True
    return has_number(entry, ("total_tokens", "tokens", "token_count"))


def entry_accounted_tokens(entry: Mapping[str, Any]) -> int:
    if has_number(entry, ("accounted_tokens",)):
        return first_number(entry, ("accounted_tokens",))
    if entry_usage_complete(entry):
        return complete_token_count(entry)
    return first_number(entry, ("estimated_tokens",))


def complete_token_count(entry: Mapping[str, Any]) -> int:
    if has_number(entry, ("total_tokens",)):
        return first_number(entry, ("total_tokens",))
    input_tokens = first_number(entry, ("input_tokens", "prompt_tokens"))
    output_tokens = first_number(entry, ("output_tokens", "completion_tokens"))
    if has_number(entry, ("input_tokens", "prompt_tokens")) and has_number(
        entry, ("output_tokens", "completion_tokens")
    ):
        return input_tokens + output_tokens
    return token_count(entry)


def snapshot_value(snapshot: Any, name: str, default: Any) -> Any:
    if isinstance(snapshot, Mapping):
        return snapshot.get(name, default)
    return getattr(snapshot, name, default)


def snapshot_number(snapshot: Any, name: str, default: int) -> int:
    value = snapshot_value(snapshot, name, default)
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else default


def token_count(entry: Mapping[str, Any]) -> int:
    for key in ("total_tokens", "tokens", "token_count"):
        value = entry.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return int(value)
    return sum(
        int(entry[key])
        for key in ("prompt_tokens", "completion_tokens")
        if isinstance(entry.get(key), (int, float)) and not isinstance(entry.get(key), bool)
    )


def metric_type(entry: Mapping[str, Any]) -> str:
    return str(entry.get("type") or entry.get("metric_type") or "")


__all__ = [
    "complete_token_count",
    "entry_accounted_tokens",
    "entry_usage_complete",
    "first_number",
    "has_number",
    "metric_type",
    "snapshot_number",
    "snapshot_value",
    "token_count",
]

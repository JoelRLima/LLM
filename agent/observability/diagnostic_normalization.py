"""Normalization helpers for immutable diagnostic records."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent.observability.redaction import (
    MAX_OBSERVATION_DATA_CHARS,
    MAX_OBSERVATION_TEXT,
    redact_observation_value,
    redact_text,
)
from agent.runtime.correlation import RunCorrelation


def selected_correlation(
    correlation: RunCorrelation | Mapping[str, Any] | None,
) -> RunCorrelation | None:
    if isinstance(correlation, RunCorrelation):
        return correlation
    if isinstance(correlation, Mapping):
        selected = RunCorrelation.from_mapping(correlation)
        if selected is None:
            raise ValueError("diagnostic correlation is invalid")
        return selected
    if correlation is None:
        return None
    raise TypeError("diagnostic correlation must be RunCorrelation, mapping, or null")


def merge_correlation(values: dict[str, Any], correlation: RunCorrelation | None) -> None:
    if correlation is None:
        return
    for name, value in correlation.as_dict().items():
        if values[name] is None:
            values[name] = value
        elif values[name] != value:
            raise ValueError(f"diagnostic {name} conflicts with correlation")


def normalized_data(data: Mapping[str, Any] | None) -> Mapping[str, Any]:
    normalized = redact_observation_value({} if data is None else data)
    if not isinstance(normalized, Mapping):
        raise TypeError("diagnostic data must be a mapping")
    return normalized


def normalized_message(message: str | None) -> str | None:
    if message is None:
        return None
    if not isinstance(message, str):
        raise TypeError("diagnostic message/summary must be a string or null")
    return redact_text(message, limit=MAX_OBSERVATION_TEXT)


__all__ = [
    "MAX_OBSERVATION_DATA_CHARS",
    "MAX_OBSERVATION_TEXT",
    "merge_correlation",
    "normalized_data",
    "normalized_message",
    "selected_correlation",
]

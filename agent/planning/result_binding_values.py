"""Causal result-shape checks and JSON-safe value projection."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent.planning.result_binding_types import ResultBindingError
from agent.tools.result_completeness import (
    canonical_completeness,
    canonical_result_successful,
)


def result_is_bindable(result: Mapping[str, Any]) -> bool:
    """Return whether a result is complete enough to become causal data."""

    if not canonical_result_successful(result) or result.get("executed") is not True:
        return False
    return "data" in result and canonical_completeness(result)[0]


def json_detach(value: Any) -> Any:
    if value is None or type(value) in (str, int, float, bool):
        return value
    if isinstance(value, Mapping):
        return {str(key): json_detach(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_detach(item) for item in value]
    raise ResultBindingError("resultado contém valor não JSON vinculável")


__all__ = ["json_detach", "result_is_bindable"]

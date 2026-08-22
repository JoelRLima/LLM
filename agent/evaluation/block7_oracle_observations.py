"""Canonical observation projections used by Block 7 oracle rules."""

from __future__ import annotations

import json
from typing import Any, Mapping, cast


def observation(report: Any) -> Any:
    value = getattr(report, "observation", None)
    if value is not None:
        return value
    return report.get("observation", {}) if isinstance(report, Mapping) else {}


def evidence(report: Any) -> Mapping[str, Any]:
    current = observation(report)
    value = getattr(current, "evidence", None)
    if isinstance(value, Mapping):
        return value
    if isinstance(current, Mapping) and isinstance(current.get("evidence"), Mapping):
        return cast(Mapping[str, Any], current["evidence"])
    return {}


def history(report: Any) -> list[Mapping[str, Any]]:
    raw = evidence(report).get("invocation_evidence", ())
    return [item for item in raw if isinstance(item, Mapping)]


def tool_names(report: Any) -> list[str]:
    return [str(item.get("tool", "")) for item in history(report)]


def answer(report: Any) -> str:
    current = observation(report)
    value = getattr(current, "answer", None)
    if value is None and isinstance(current, Mapping):
        value = current.get("answer", "")
    return str(value or "")


def observation_flags(result: Mapping[str, Any]) -> tuple[bool, bool | None, bool]:
    data_present = "data" in result and result.get("data") is not None
    complete = result.get("complete") if type(result.get("complete")) is bool else None
    truncated = bool(result.get("truncated", False))
    for artifact in result.get("artifacts", ()):
        if not isinstance(artifact, Mapping):
            continue
        metadata = artifact.get("metadata")
        if not isinstance(metadata, Mapping):
            continue
        if type(metadata.get("complete")) is bool:
            complete = metadata["complete"]
        if type(metadata.get("truncated")) is bool:
            truncated = metadata["truncated"]
    if complete is None and truncated:
        complete = False
    return data_present, complete, truncated


def result_status(item: Mapping[str, Any], result: Mapping[str, Any]) -> str:
    return str(result.get("status") or item.get("status") or "")


def canonical_plan(report: Any) -> list[Mapping[str, Any]]:
    raw = evidence(report).get("canonical_plan", ())
    return [step for step in raw if isinstance(step, Mapping)]


def _contains_duplicate(value: Any) -> bool:
    if isinstance(value, Mapping):
        args = value.get("args")
        bindings = value.get("bindings")
        if isinstance(args, Mapping) and isinstance(bindings, Mapping) and set(args) & set(bindings):
            return True
        return any(_contains_duplicate(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_duplicate(item) for item in value)
    return False


def _record_duplicate(record: Mapping[str, Any]) -> bool:
    response = record.get("response")
    if isinstance(response, str):
        try:
            if _contains_duplicate(json.loads(response)):
                return True
        except (TypeError, ValueError):
            pass
    return _contains_duplicate(record)


def raw_duplicate_detected(report: Any) -> bool:
    """Find a duplicate args/bindings representation in bounded raw decisions."""

    return any(
        _record_duplicate(record)
        for key in ("model_decisions", "repair_decisions", "route_decisions")
        for record in evidence(report).get(key, ())
        if isinstance(record, Mapping)
    )


def validation_evidence(report: Any) -> list[Mapping[str, Any]]:
    raw = evidence(report).get("validation_evidence", ())
    return [item for item in raw if isinstance(item, Mapping)]


__all__ = [
    "answer", "canonical_plan", "evidence", "history", "observation",
    "observation_flags", "raw_duplicate_detected", "result_status",
    "tool_names", "validation_evidence",
]

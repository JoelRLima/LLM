"""Bounded canonical facts for invocation commit anomalies."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

CANONICAL_COMMIT_FAILED = "CANONICAL_COMMIT_FAILED"
EFFECT_PROVEN = "PROVEN"
EFFECT_NONE = "NONE"
EFFECT_UNKNOWN = "UNKNOWN"
EFFECT_STATES = frozenset({EFFECT_PROVEN, EFFECT_NONE, EFFECT_UNKNOWN})
MAX_EXECUTION_INCIDENTS = 64
MAX_INCIDENT_ID_CHARS = 128
MAX_INCIDENT_TOOL_CHARS = 128
MAX_INCIDENT_STATUS_CHARS = 32
MAX_INCIDENT_ERROR_CODE_CHARS = 64
MAX_INCIDENT_FILE_CHARS = 512
MAX_INCIDENT_FILES = 128

_INCIDENT_KEYS = frozenset(
    {
        "incident_type",
        "invocation_id",
        "tool",
        "original_tool_status",
        "executed",
        "effect_state",
        "affected_files",
        "rollback_occurred",
        "error_code",
    }
)


def _bounded_text(value: Any, *, field: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"execution incident field is invalid: {field}")
    value = value.strip()
    if len(value) > limit:
        raise ValueError(f"execution incident field is too long: {field}")
    return str(value)


def _optional_bool(value: Any, *, field: str) -> bool | None:
    if value is not None and type(value) is not bool:
        raise ValueError(f"execution incident {field} field is invalid")
    return value


def _affected_files(value: Any) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ValueError("execution incident affected_files field is invalid")
    if len(value) > MAX_INCIDENT_FILES:
        raise ValueError("execution incident affected_files field is too large")
    files: list[str] = []
    for raw_file in value:
        file_path = _bounded_text(
            raw_file,
            field="affected_files",
            limit=MAX_INCIDENT_FILE_CHARS,
        )
        if file_path not in files:
            files.append(file_path)
    return files


def normalize_execution_incident(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and retain only the minimal incident schema."""

    if not isinstance(value, Mapping):
        raise ValueError("execution incident must be an object")
    if set(value) - _INCIDENT_KEYS:
        raise ValueError("execution incident contains unsupported fields")
    incident_type = _bounded_text(
        value.get("incident_type"),
        field="incident_type",
        limit=MAX_INCIDENT_ERROR_CODE_CHARS,
    )
    if incident_type != CANONICAL_COMMIT_FAILED:
        raise ValueError("execution incident type is unsupported")
    invocation_id = _bounded_text(
        value.get("invocation_id"),
        field="invocation_id",
        limit=MAX_INCIDENT_ID_CHARS,
    )
    tool = _bounded_text(
        value.get("tool"),
        field="tool",
        limit=MAX_INCIDENT_TOOL_CHARS,
    )
    original_status = _bounded_text(
        value.get("original_tool_status"),
        field="original_tool_status",
        limit=MAX_INCIDENT_STATUS_CHARS,
    )
    executed = _optional_bool(value.get("executed"), field="executed")
    effect_state = _bounded_text(
        value.get("effect_state"),
        field="effect_state",
        limit=MAX_INCIDENT_ERROR_CODE_CHARS,
    )
    if effect_state not in EFFECT_STATES:
        raise ValueError("execution incident effect state is unsupported")
    affected_files = _affected_files(value.get("affected_files"))
    rollback = _optional_bool(value.get("rollback_occurred"), field="rollback")
    error_code = _bounded_text(
        value.get("error_code"),
        field="error_code",
        limit=MAX_INCIDENT_ERROR_CODE_CHARS,
    )
    return {
        "incident_type": incident_type,
        "invocation_id": invocation_id,
        "tool": tool,
        "original_tool_status": original_status,
        "executed": executed,
        "effect_state": effect_state,
        "affected_files": affected_files,
        "rollback_occurred": rollback,
        "error_code": error_code,
    }


def normalize_execution_incidents(value: Any) -> list[dict[str, Any]]:
    """Validate a bounded checkpoint or state journal fail-closed."""

    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("execution incidents must be a list")
    if len(value) > MAX_EXECUTION_INCIDENTS:
        raise ValueError("execution incident journal exceeds its bound")
    return [normalize_execution_incident(item) for item in value]


__all__ = [
    "CANONICAL_COMMIT_FAILED",
    "EFFECT_NONE",
    "EFFECT_PROVEN",
    "EFFECT_UNKNOWN",
    "EFFECT_STATES",
    "MAX_EXECUTION_INCIDENTS",
    "normalize_execution_incident",
    "normalize_execution_incidents",
]

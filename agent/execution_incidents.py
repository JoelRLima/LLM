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
MAX_INCIDENT_OMITTED = 1_000_000

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
        "detail_truncated",
        "normalization_failed",
        "journal_overflow",
        "omitted_incidents",
        "omitted_effect_states",
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


def _optional_nonnegative_int(value: Any, *, field: str) -> int | None:
    if value is None:
        return None
    if type(value) is not int or value < 0 or value > MAX_INCIDENT_OMITTED:
        raise ValueError(f"execution incident {field} field is invalid")
    return value


def _optional_effect_states(value: Any) -> list[str] | None:
    if value is None:
        return None
    if not isinstance(value, (list, tuple)) or len(value) > len(EFFECT_STATES):
        raise ValueError("execution incident omitted_effect_states field is invalid")
    states: list[str] = []
    for raw_state in value:
        if not isinstance(raw_state, str) or raw_state not in EFFECT_STATES:
            raise ValueError("execution incident omitted effect state is unsupported")
        if raw_state not in states:
            states.append(raw_state)
    return states


def _affected_files(value: Any) -> tuple[list[str], bool]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ValueError("execution incident affected_files field is invalid")
    truncated = len(value) > MAX_INCIDENT_FILES
    files: list[str] = []
    for raw_file in value:
        if len(files) >= MAX_INCIDENT_FILES:
            break
        file_path = _bounded_text(
            raw_file,
            field="affected_files",
            limit=MAX_INCIDENT_FILE_CHARS,
        )
        if file_path not in files:
            files.append(file_path)
    return files, truncated


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
    affected_files, files_truncated = _affected_files(value.get("affected_files"))
    rollback = _optional_bool(value.get("rollback_occurred"), field="rollback")
    error_code = _bounded_text(
        value.get("error_code"),
        field="error_code",
        limit=MAX_INCIDENT_ERROR_CODE_CHARS,
    )
    detail_truncated = _optional_bool(
        value.get("detail_truncated"), field="detail_truncated"
    )
    normalization_failed = _optional_bool(
        value.get("normalization_failed"), field="normalization_failed"
    )
    journal_overflow = _optional_bool(
        value.get("journal_overflow"), field="journal_overflow"
    )
    omitted_incidents = _optional_nonnegative_int(
        value.get("omitted_incidents"), field="omitted_incidents"
    )
    omitted_effect_states = _optional_effect_states(value.get("omitted_effect_states"))
    normalized: dict[str, Any] = {
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
    if files_truncated or detail_truncated is True:
        normalized["detail_truncated"] = True
    if normalization_failed is not None:
        normalized["normalization_failed"] = normalization_failed
    if journal_overflow is not None:
        normalized["journal_overflow"] = journal_overflow
    if omitted_incidents is not None:
        normalized["omitted_incidents"] = omitted_incidents
    if omitted_effect_states is not None:
        normalized["omitted_effect_states"] = omitted_effect_states
    return normalized


def _safe_text(value: Any, default: str, limit: int) -> str:
    if isinstance(value, str):
        text = value.strip()
        if text:
            return text[:limit]
    return default


def _safe_files(value: Any) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    files: list[str] = []
    for raw_file in value:
        if not isinstance(raw_file, str):
            continue
        file_path = raw_file.strip()
        if not file_path or len(file_path) > MAX_INCIDENT_FILE_CHARS:
            continue
        if file_path not in files:
            files.append(file_path)
        if len(files) >= MAX_INCIDENT_FILES:
            break
    return files


def fail_closed_execution_incident(value: Any) -> dict[str, Any]:
    """Build a bounded incident when normal detail validation cannot finish."""

    raw = value if isinstance(value, Mapping) else {}
    raw_effect = raw.get("effect_state")
    effect_state = EFFECT_PROVEN if raw_effect == EFFECT_PROVEN else EFFECT_UNKNOWN
    return {
        "incident_type": CANONICAL_COMMIT_FAILED,
        "invocation_id": _safe_text(
            raw.get("invocation_id"), "unknown-invocation", MAX_INCIDENT_ID_CHARS
        ),
        "tool": _safe_text(raw.get("tool"), "unknown-tool", MAX_INCIDENT_TOOL_CHARS),
        "original_tool_status": _safe_text(
            raw.get("original_tool_status"), "unverified", MAX_INCIDENT_STATUS_CHARS
        ),
        "executed": raw.get("executed") if type(raw.get("executed")) is bool else None,
        "effect_state": effect_state,
        "affected_files": _safe_files(raw.get("affected_files")) if effect_state == EFFECT_PROVEN else [],
        "rollback_occurred": (
            raw.get("rollback_occurred")
            if type(raw.get("rollback_occurred")) is bool
            else None
        ),
        "error_code": CANONICAL_COMMIT_FAILED,
        "detail_truncated": True,
        "normalization_failed": True,
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
    "MAX_INCIDENT_OMITTED",
    "fail_closed_execution_incident",
    "normalize_execution_incident",
    "normalize_execution_incidents",
]

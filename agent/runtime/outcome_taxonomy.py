"""Canonical operational statuses and structured error classifications.

This module owns classification metadata only. Human-facing messages remain
with the domain that has enough context to render them safely.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from agent.runtime.outcome_error_registry import build_error_registry


class OperationalStatus(str, Enum):
    SUCCEEDED = "succeeded"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    PERMISSION_DENIED = "permission_denied"
    PROTOCOL_ERROR = "protocol_error"
    UNAVAILABLE = "unavailable"
    UNVERIFIED = "unverified"


PUBLIC_TERMINAL_STATUSES = frozenset(item.value for item in OperationalStatus)
NON_SUCCESS_STATUSES = frozenset(
    item for item in PUBLIC_TERMINAL_STATUSES if item != OperationalStatus.SUCCEEDED.value
)
LOCAL_FAILURE_STATUSES = frozenset(
    {
        OperationalStatus.FAILED.value,
        OperationalStatus.TIMED_OUT.value,
        OperationalStatus.UNAVAILABLE.value,
    }
)


class FailureLayer(str, Enum):
    PROVIDER = "provider"
    GATEWAY = "gateway"
    TOOL = "tool"
    RUNTIME = "runtime"


@dataclass(frozen=True, slots=True)
class ErrorDefinition:
    """Classification metadata for one public or policy-relevant code."""

    code: str
    layer: FailureLayer = FailureLayer.RUNTIME
    public_safe: bool = True
    hard: bool = False
    default_status: str | None = None
    retryable: bool = False


_STATUS_ALIASES = {
    "complete": OperationalStatus.SUCCEEDED.value,
    "completed": OperationalStatus.SUCCEEDED.value,
    "success": OperationalStatus.SUCCEEDED.value,
    "block": OperationalStatus.BLOCKED.value,
    "skipped": OperationalStatus.BLOCKED.value,
    "fail": OperationalStatus.FAILED.value,
}


def operational_status_for(value: object) -> str | None:
    """Map a lifecycle/serialized status to the canonical terminal value."""

    raw = getattr(value, "value", value)
    if not isinstance(raw, str):
        return None
    normalized = raw.strip().casefold()
    try:
        return OperationalStatus(normalized).value
    except ValueError:
        return _STATUS_ALIASES.get(normalized)


ERROR_DEFINITIONS = build_error_registry(ErrorDefinition, FailureLayer, OperationalStatus)
PUBLIC_ERROR_CODES = frozenset(
    code for code, definition in ERROR_DEFINITIONS.items() if definition.public_safe
)
HARD_FAILURE_CODES = frozenset(
    code for code, definition in ERROR_DEFINITIONS.items() if definition.hard
)
HARD_FAILURE_STATUSES = frozenset(
    item.value
    for item in (
        OperationalStatus.BLOCKED,
        OperationalStatus.CANCELLED,
        OperationalStatus.PERMISSION_DENIED,
        OperationalStatus.PROTOCOL_ERROR,
        OperationalStatus.UNVERIFIED,
    )
)


def error_definition(code: str | None) -> ErrorDefinition | None:
    return ERROR_DEFINITIONS.get(str(code)) if code else None


def failure_layer_for_code(code: str | None) -> str:
    definition = error_definition(code)
    return definition.layer.value if definition is not None else FailureLayer.RUNTIME.value


__all__ = [
    "ERROR_DEFINITIONS",
    "ErrorDefinition",
    "FailureLayer",
    "HARD_FAILURE_CODES",
    "HARD_FAILURE_STATUSES",
    "LOCAL_FAILURE_STATUSES",
    "NON_SUCCESS_STATUSES",
    "OperationalStatus",
    "PUBLIC_ERROR_CODES",
    "PUBLIC_TERMINAL_STATUSES",
    "error_definition",
    "failure_layer_for_code",
    "operational_status_for",
]

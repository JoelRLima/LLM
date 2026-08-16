"""Canonical, bounded projection of tool observations for model context."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from agent.tools.result_completeness import canonical_completeness

MAX_OBSERVATION_EVIDENCE_CHARS = 12_000
MAX_OBSERVATION_RECORD_CHARS = 2_000
MAX_INVOCATION_ARGS_CHARS = 1_000
PUBLIC_TOOL_ERROR_CODES = frozenset(
    {
        "ADAPTER_FAILED", "APPLICATION_AUTHORITY_DENIED", "APPLICATION_AUTHORITY_MISSING",
        "APPROVAL_DENIED", "APPROVAL_FAILED", "APPROVAL_REQUIRED", "AUTHORITY_REQUIRED",
        "CANCELLED", "DUPLICATE_INVOCATION_ID", "EXECUTION_ERROR", "INVALID_ARGUMENTS",
        "INVALID_RESPONSE", "INVALID_RESULT", "INVALID_STATUS", "INVOCATION_ID_MISMATCH",
        "ORIGIN_MISMATCH", "PERMISSION_DENIED", "REGISTRY_UNBOUND", "REQUEST_INVALID",
        "RUNTIME_MISMATCH", "TASK_AUTHORITY_DENIED", "TASK_AUTHORITY_MISSING", "TIMEOUT",
        "TOOL_ERROR", "TOOL_NOT_FOUND", "WORKSPACE_GRANT_DENIED",
    }
)
PUBLIC_TOOL_STATUSES = frozenset(
    {
        "blocked", "cancelled", "failed", "permission_denied", "protocol_error",
        "succeeded", "timed_out", "unavailable", "unverified",
    }
)


def _safe_identity(value: Any, limit: int) -> str:
    return re.sub(r"[^A-Za-z0-9_.:@/+-]", "?", str(value))[:limit]


def _value_type(value: Any) -> str:
    if value is None:
        return "null"
    if type(value) is bool:
        return "boolean"
    if type(value) in (int, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, (list, tuple)):
        return "array"
    if isinstance(value, Mapping):
        return "object"
    return "unknown"


@dataclass(frozen=True, slots=True)
class ObservationEvidence:
    tool: str
    invocation_id: str | None
    status: str
    ok: bool | None
    executed: bool | None
    error_code: str | None
    present: bool
    value_type: str
    value: Any
    chars: int | None
    source_complete: bool | None
    source_truncated: bool

    @property
    def complete(self) -> bool:
        return self.present and self.source_complete is True and not self.source_truncated

    @property
    def truncated(self) -> bool:
        return self.source_truncated

    def base_record(self) -> dict[str, Any]:
        record: dict[str, Any] = {
            "tool": self.tool,
            "status": self.status,
            "observation": {
                "present": self.present,
                "type": self.value_type,
                "complete": self.complete,
                "truncated": self.truncated,
            },
        }
        for key, value in (
            ("invocation_id", self.invocation_id),
            ("ok", self.ok),
            ("executed", self.executed),
            ("error_code", self.error_code),
        ):
            if value is not None:
                record[key] = value
        if self.chars is not None:
            record["observation"]["chars"] = self.chars
        if self.source_complete is False:
            record["observation"]["source_complete"] = False
        return record


def project_tool_observation(entry: Mapping[str, Any]) -> ObservationEvidence:
    raw_result = entry.get("result")
    result = raw_result if isinstance(raw_result, Mapping) else {}
    present = "data" in result
    value = result.get("data") if present else None
    source_complete, source_truncated = canonical_completeness(result)
    raw_status = result.get("status")
    status = raw_status if isinstance(raw_status, str) and raw_status in PUBLIC_TOOL_STATUSES else "unknown"
    raw_error_code = result.get("error_code")
    error_code = raw_error_code if isinstance(raw_error_code, str) and raw_error_code in PUBLIC_TOOL_ERROR_CODES else None
    invocation = entry.get("invocation_id") or result.get("invocation_id")
    return ObservationEvidence(
        tool=_safe_identity(entry.get("tool", ""), 32),
        invocation_id=_safe_identity(invocation, 128) if invocation is not None else None,
        status=status,
        ok=result.get("ok") if type(result.get("ok")) is bool else None,
        executed=result.get("executed") if type(result.get("executed")) is bool else None,
        error_code=error_code,
        present=present,
        value_type=_value_type(value) if present else "missing",
        value=value,
        chars=len(value) if isinstance(value, str) else None,
        source_complete=source_complete if present else None,
        source_truncated=source_truncated if present else False,
    )


def project_executed_invocation(*args: Any, **kwargs: Any) -> dict[str, Any]:
    from .invocation_evidence import project_executed_invocation as project

    return project(*args, **kwargs)


def serialize_tool_observations(*args: Any, **kwargs: Any) -> str:
    from .observation_serialization import serialize_tool_observations as serialize

    return serialize(*args, **kwargs)


def observation_contract_instructions() -> str:
    from .observation_serialization import observation_contract_instructions as instructions

    return instructions()


__all__ = [
    "MAX_INVOCATION_ARGS_CHARS", "MAX_OBSERVATION_EVIDENCE_CHARS", "MAX_OBSERVATION_RECORD_CHARS",
    "ObservationEvidence", "PUBLIC_TOOL_ERROR_CODES", "PUBLIC_TOOL_STATUSES",
    "observation_contract_instructions", "project_executed_invocation", "project_tool_observation",
    "serialize_tool_observations",
]

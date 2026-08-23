"""Canonical, bounded projection of tool observations for model context."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from agent.reporting.artifact_projection import (
    ArtifactEvidence,
    metadata_is_persisted_mutation,
    project_artifact_evidence,
)
from agent.tools.result_completeness import (
    EvidenceProvenance,
    canonical_completeness,
    canonical_evidence_provenance,
    has_explicit_evidence_provenance,
)

MAX_OBSERVATION_EVIDENCE_CHARS = 12_000
MAX_OBSERVATION_RECORD_CHARS = 2_000
MAX_INVOCATION_ARGS_CHARS = 1_000
PUBLIC_TOOL_ERROR_CODES = frozenset(
    {
        "ADAPTER_FAILED", "APPLICATION_AUTHORITY_DENIED", "APPLICATION_AUTHORITY_MISSING",
        "APPROVAL_DENIED", "APPROVAL_FAILED", "APPROVAL_REQUIRED", "AUTHORITY_REQUIRED",
        "AUTH_DENIED", "AUTH_REQUIRED", "CANCELLED", "DENIED", "DUPLICATE_INVOCATION_ID",
        "EXECUTION_ABORTED", "EXECUTION_ERROR", "INVALID_ARGUMENTS",
        "INVALID_RESPONSE", "INVALID_RESULT", "INVALID_STATUS", "INVOCATION_ID_MISMATCH",
        "MISSING_REQUIRED_INPUT", "ORIGIN_MISMATCH", "PERMISSION_DENIED", "PROVIDER_FAILED",
        "REGISTRY_UNBOUND", "REQUEST_INVALID",
        "RUNTIME_MISMATCH", "TASK_AUTHORITY_DENIED", "TASK_AUTHORITY_MISSING", "TIMEOUT",
        "TASK_BUDGET_EXHAUSTED", "TASK_CLEANUP_FAILURE", "TASK_COST_LIMIT_REACHED",
        "TOOL_ERROR", "TOOL_NOT_FOUND", "UNRESOLVED_SYMBOLIC_ARGUMENT", "WATCHDOG_TIMEOUT",
        "WORKSPACE_GRANT_DENIED", "prepared_invocation_stale", "reasoning_boundary_blocked",
        "requested_effect_pending", "task_obligation_pending", "unresolved_symbolic_argument",
    }
)
PUBLIC_TOOL_STATUSES = frozenset(
    {
        "blocked", "cancelled", "failed", "permission_denied", "protocol_error",
        "succeeded", "timed_out", "unavailable", "unverified",
    }
)
_NON_SUCCESS_STATUSES = PUBLIC_TOOL_STATUSES - {"succeeded"}


def result_status(result: Mapping[str, Any]) -> str:
    """Return the bounded public status of one recorded tool result."""

    raw_status = result.get("status")
    return raw_status if isinstance(raw_status, str) and raw_status in PUBLIC_TOOL_STATUSES else "unknown"


def result_is_successful(result: Mapping[str, Any]) -> bool:
    """Whether the recorded result is a canonical successful observation."""

    return result_status(result) == "succeeded" and result.get("ok") is True


def result_is_failed(result: Mapping[str, Any]) -> bool:
    """Whether the result carries an explicit failure boundary."""

    return result_status(result) in _NON_SUCCESS_STATUSES or result.get("ok") is False


def result_executed(result: Mapping[str, Any]) -> bool | None:
    """Return execution truth without treating an omitted flag as false."""

    value = result.get("executed")
    return value if type(value) is bool else None


def result_error_code(result: Mapping[str, Any]) -> str | None:
    """Expose only known public error-code values."""

    value = result.get("error_code")
    return value if isinstance(value, str) and value in PUBLIC_TOOL_ERROR_CODES else None


def result_has_data(result: Mapping[str, Any]) -> bool:
    """Structural presence of a result value, including empty/null values."""

    return "data" in result


def _artifact_metadata_from(value: Any) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, Mapping):
        return ()
    artifacts = value.get("artifacts")
    if not isinstance(artifacts, Sequence) or isinstance(artifacts, (str, bytes, bytearray)):
        return ()
    return tuple(
        dict(item["metadata"])
        for item in artifacts
        if isinstance(item, Mapping) and isinstance(item.get("metadata"), Mapping)
    )


def artifact_metadata(result: Any) -> tuple[dict[str, Any], ...]:
    """Return artifact metadata from both supported legacy result shapes."""

    if not isinstance(result, Mapping):
        return ()
    values = list(_artifact_metadata_from(result))
    data = result.get("data")
    if isinstance(data, Mapping):
        values.extend(_artifact_metadata_from(data))
    for candidate in (result, data):
        if not isinstance(candidate, Mapping):
            continue
        metadata = candidate.get("metadata")
        if isinstance(metadata, Mapping):
            values.append(dict(metadata))
    return tuple(values)


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
    provenance: EvidenceProvenance
    provenance_explicit: bool = False

    @property
    def complete(self) -> bool:
        return self.present and self.source_complete is True and not self.source_truncated

    @property
    def truncated(self) -> bool:
        return self.source_truncated

    def base_record(self) -> dict[str, Any]:
        observation: dict[str, Any] = {
            "present": self.present,
            "type": self.value_type,
            "complete": self.complete,
            "truncated": self.truncated,
        }
        if self.present and (
            self.provenance_explicit or self.provenance is not EvidenceProvenance.UNKNOWN
        ):
            observation["provenance"] = self.provenance.value
        record: dict[str, Any] = {
            "tool": self.tool,
            "status": self.status,
            "observation": observation,
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
    present = result_has_data(result)
    value = result.get("data") if present else None
    source_complete, source_truncated = canonical_completeness(result)
    provenance = canonical_evidence_provenance(result)
    status = result_status(result)
    error_code = result_error_code(result)
    invocation = entry.get("invocation_id") or result.get("invocation_id")
    return ObservationEvidence(
        tool=_safe_identity(entry.get("tool", ""), 32),
        invocation_id=_safe_identity(invocation, 128) if invocation is not None else None,
        status=status,
        ok=result.get("ok") if type(result.get("ok")) is bool else None,
        executed=result_executed(result),
        error_code=error_code,
        present=present,
        value_type=_value_type(value) if present else "missing",
        value=value,
        chars=len(value) if isinstance(value, str) else None,
        source_complete=source_complete if present else None,
        source_truncated=source_truncated if present else False,
        provenance=provenance,
        provenance_explicit=has_explicit_evidence_provenance(result),
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
    "ArtifactEvidence", "MAX_INVOCATION_ARGS_CHARS", "MAX_OBSERVATION_EVIDENCE_CHARS",
    "MAX_OBSERVATION_RECORD_CHARS", "ObservationEvidence", "EvidenceProvenance",
    "PUBLIC_TOOL_ERROR_CODES",
    "PUBLIC_TOOL_STATUSES", "artifact_metadata", "metadata_is_persisted_mutation",
    "observation_contract_instructions", "project_executed_invocation", "project_tool_observation",
    "project_artifact_evidence", "result_error_code", "result_executed", "result_has_data",
    "result_is_failed", "result_is_successful", "result_status", "serialize_tool_observations",
]

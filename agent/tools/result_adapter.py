"""Bounded persistence adapters at state, history, and extension boundaries."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast
from uuid import uuid4

from agent.contracts import LegacyToolResult
from agent.tools.contracts import ToolError, ToolResult, ToolStatus


def to_legacy_result(result: Any) -> LegacyToolResult:
    """Project a canonical result only when a legacy mapping is required."""

    if isinstance(result, ToolResult):
        return cast(LegacyToolResult, result.to_legacy_dict(include_details=True))
    if isinstance(result, Mapping):
        return cast(LegacyToolResult, dict(result))
    raise TypeError("resultado de ferramenta nao suportado")


def from_legacy_result(result: Any) -> ToolResult:
    """Adapt one persisted or boundary mapping into the canonical result.

    This is intentionally the only reverse adapter used at state/history
    boundaries.  Unknown legacy fields are retained as non-authoritative
    metadata so completeness and source identity are not silently discarded.
    """

    if isinstance(result, ToolResult):
        return result
    if not isinstance(result, Mapping):
        raise TypeError("resultado de ferramenta nao suportado")

    invocation_id = result.get("invocation_id")
    if not isinstance(invocation_id, str) or not invocation_id.strip():
        invocation_id = f"legacy:{uuid4().hex}"

    raw_status = result.get("status")
    try:
        status = ToolStatus(str(raw_status)) if raw_status is not None else None
    except ValueError:
        status = ToolStatus.PROTOCOL_ERROR
    if status is None:
        status = ToolStatus.SUCCEEDED if result.get("ok") is True else ToolStatus.FAILED

    raw_error = result.get("error")
    raw_detail = result.get("error_detail")
    raw_code = result.get("error_code")
    if isinstance(raw_error, Mapping):
        raw_error_detail = raw_error.get("detail")
        error = ToolError(
            str(raw_error.get("code") or raw_code or "TOOL_ERROR"),
            str(raw_error.get("message") or raw_error),
            dict(cast(Mapping[str, Any], raw_error_detail))
            if isinstance(raw_error_detail, Mapping)
            else None,
        )
    elif raw_error is not None:
        detail = (
            dict(cast(Mapping[str, Any], raw_detail))
            if isinstance(raw_detail, Mapping)
            else None
        )
        error = ToolError(
            str(raw_code or "TOOL_ERROR"),
            str(raw_error),
            detail,
        )
    elif raw_code is not None:
        error = ToolError(
            str(raw_code),
            str(result.get("message") or raw_code),
            dict(cast(Mapping[str, Any], raw_detail))
            if isinstance(raw_detail, Mapping)
            else None,
        )
    else:
        error = None

    raw_artifacts = result.get("artifacts")
    artifacts = (
        tuple(raw_artifacts)
        if isinstance(raw_artifacts, (list, tuple))
        else ()
    )
    known = {
        "invocation_id", "ok", "done", "status", "executed", "data", "error",
        "error_code", "error_detail", "message", "artifacts", "evidence_provenance",
    }
    metadata = {
        str(key): value
        for key, value in result.items()
        if key not in known
    }
    provenance = result.get("evidence_provenance")
    return ToolResult(
        invocation_id=invocation_id,
        status=status,
        data=result.get("data"),
        error=error,
        message=str(result["message"]) if result.get("message") is not None else None,
        artifacts=artifacts,
        executed=result.get("executed") if type(result.get("executed")) is bool else None,
        evidence_provenance=str(provenance) if provenance is not None else None,
        metadata=metadata,
        done_override=result.get("done") if type(result.get("done")) is bool else None,
    )


def ensure_canonical_result(result: Any) -> ToolResult:
    """Return a canonical result without re-adapting an existing value.

    Boundary callers may receive a historical mapping or an extension result.
    A canonical result passes through unchanged; only a mapping is upgraded.
    """

    return result if isinstance(result, ToolResult) else from_legacy_result(result)


__all__ = ["ensure_canonical_result", "from_legacy_result", "to_legacy_result"]

"""Minimal public projection of one canonical agent run."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from agent.llm.model_client import ModelProviderError


def failure_layer_for_code(code: str | None) -> str:
    if code == "MODEL_PROVIDER_ERROR":
        return "provider"
    if code in {
        "APPLICATION_AUTHORITY_MISSING", "APPLICATION_AUTHORITY_DENIED",
        "TASK_AUTHORITY_MISSING", "TASK_AUTHORITY_DENIED",
        "WORKSPACE_GRANT_DENIED", "RUNTIME_MISMATCH", "APPROVAL_REQUIRED",
        "APPROVAL_DENIED", "PERMISSION_DENIED",
    }:
        return "gateway"
    if code in {"TOOL_ERROR", "EXECUTION_ERROR", "TOOL_NOT_FOUND"}:
        return "tool"
    return "runtime"


def _artifact_metadata(data: Any) -> list[dict[str, Any]]:
    if not isinstance(data, dict):
        return []
    artifacts = data.get("artifacts")
    if not isinstance(artifacts, (list, tuple)):
        return []
    return [
        item["metadata"]
        for item in artifacts
        if isinstance(item, dict) and isinstance(item.get("metadata"), dict)
    ]


def _project_tool(entry: dict[str, Any]) -> tuple[dict[str, Any], bool | None, list[dict[str, Any]]]:
    raw = cast(dict[str, Any], entry.get("result") if isinstance(entry.get("result"), dict) else {})
    status = str(raw.get("status") or entry.get("status") or "")
    executed = raw.get("executed")
    tool = {
        "tool": str(entry.get("tool") or ""),
        "invocation_id": entry.get("invocation_id") or raw.get("invocation_id"),
        "status": status,
        "executed": executed,
        "error_code": raw.get("error_code"),
    }
    metadata = _artifact_metadata(raw.get("data"))
    return tool, executed, metadata


def _collect_artifact_state(
    metadata: list[dict[str, Any]],
    files: set[str],
    proposed: set[str],
    validation: dict[str, Any],
    rollback: dict[str, Any],
) -> None:
    for item in metadata:
        affected = item.get("affected_files")
        if isinstance(affected, (list, tuple)):
            proposed.update(str(path) for path in affected)
            if item.get("applied") is True:
                files.update(str(path) for path in affected)
        if item.get("validation") is not None:
            validation["ran"] = True
            validation["outcome"] = str(item["validation"])
        if item.get("rollback_occurred") is True:
            rollback["occurred"] = True
            rollback["outcome"] = str(item.get("final_state") or "restored")


def build_run_receipt(
    workspace: str | Path,
    state: Any,
    status: str,
    error: str | None,
    *,
    failure_code: str | None = None,
    failure_layer: str | None = None,
) -> dict[str, Any]:
    history = [
        item for item in (getattr(state, "tool_history", None) or [])
        if isinstance(item, dict)
    ]
    tools: list[dict[str, Any]] = []
    files: set[str] = set()
    proposed: set[str] = set()
    validation = {"ran": False, "outcome": None}
    rollback = {"occurred": False, "outcome": None}
    effects: list[bool] = []
    for entry in history:
        tool, effect, metadata = _project_tool(entry)
        tools.append(tool)
        if isinstance(effect, bool):
            effects.append(effect)
        _collect_artifact_state(metadata, files, proposed, validation, rollback)

    events = getattr(state, "events", None) or []
    replan_count = sum(
        1 for event in events
        if isinstance(event, dict) and event.get("type") == "replan"
    )
    repair_count = sum(
        1 for entry in history
        if isinstance(entry.get("args"), dict)
        and entry["args"].get("action") == "repair"
    )
    last_result = getattr(state, "last_result", None) or {}
    raw_code = last_result.get("error_code") if isinstance(last_result, dict) else None
    raw_detail = last_result.get("error_detail") if isinstance(last_result, dict) else None
    code = failure_code or raw_code
    cause = None
    if error or code:
        cause = {
            "message": error or str(last_result.get("error") or ""),
            "code": code or "RUN_FAILED",
            "layer": failure_layer or failure_layer_for_code(code),
        }
        if isinstance(raw_detail, dict):
            cause["detail"] = dict(raw_detail)
    executed: bool | None = (
        effects[-1] if len(effects) == 1 else (any(effects) if effects else None)
    )
    replan = {"occurred": True, "count": replan_count} if replan_count else None
    final_state = "restored" if rollback["occurred"] else ("applied" if files else None)
    return {
        "workspace": str(workspace),
        "tools": tools,
        "files_affected": sorted(files),
        "proposed_files": sorted(proposed - files),
        "validation": validation,
        "rollback": rollback,
        "final_state": final_state,
        "repair": {"occurred": repair_count > 0, "count": repair_count},
        "replan": replan,
        "error": cause,
        "executed": executed,
        "status": status,
    }


def build_run_diagnostics(
    state: Any,
    error: str | None,
    *,
    failure_code: str | None = None,
    failure_layer: str | None = None,
) -> tuple[dict[str, Any], ...]:
    diagnostics: list[dict[str, Any]] = []
    last = getattr(state, "last_result", None) or {}
    observed_code = last.get("error_code") if isinstance(last, dict) else None
    code = failure_code or observed_code
    if error or code:
        diagnostics.append({
            "layer": failure_layer or failure_layer_for_code(code),
            "code": code or "RUN_FAILED",
            "message": error or "A execucao nao foi concluida.",
            "executed": last.get("executed") if isinstance(last.get("executed"), bool) else None,
        })
    last_result = getattr(state, "last_result", None) or {}
    data = last_result.get("data") if isinstance(last_result, dict) else None
    raw = data.get("diagnostics") if isinstance(data, dict) else None
    for item in raw[:20] if isinstance(raw, (list, tuple)) else []:
        if isinstance(item, dict):
            projected = {
                key: item[key]
                for key in ("code", "message", "severity", "file_path", "source")
                if key in item
            }
            if projected:
                diagnostics.append(projected)
    return tuple(diagnostics)


def derive_status(orchestrator: Any) -> str:
    last_result = orchestrator.agent_state.last_result or {}
    tool_status = str(last_result.get("status") or "")
    if tool_status in {
        "blocked", "unverified", "cancelled", "failed", "timed_out",
        "permission_denied", "protocol_error", "unavailable",
    }:
        return tool_status
    if orchestrator._cancelled:
        return "cancelled"
    if orchestrator._task_failed:
        return "failed"
    return "succeeded"


def derive_error(orchestrator: Any, status: str) -> str | None:
    if status == "succeeded":
        return None
    last_result = orchestrator.agent_state.last_result or {}
    return str(last_result.get("error")) if last_result.get("error") else None


def public_exception_message(exc: BaseException) -> str:
    if isinstance(exc, ModelProviderError):
        return str(exc.public_message)
    text = str(exc).strip()
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


def finalize_run_result(
    result_type: Any,
    workspace: str | Path,
    orchestrator: Any,
    status: str,
    answer: str,
    *,
    error: str | None = None,
    diagnostics: tuple[dict[str, Any], ...] = (),
    metadata: dict[str, Any] | None = None,
    receipt: dict[str, Any] | None = None,
    report_path: str | None = None,
) -> Any:
    failure_code = getattr(orchestrator, "_last_failure_code", None)
    failure_layer = getattr(orchestrator, "_last_failure_layer", None)
    effective_receipt = receipt or build_run_receipt(
        workspace, orchestrator.agent_state, status, error,
        failure_code=failure_code, failure_layer=failure_layer,
    )
    effective_diagnostics = diagnostics or build_run_diagnostics(
        orchestrator.agent_state, error,
        failure_code=failure_code, failure_layer=failure_layer,
    )
    effective_report_path = report_path
    report_builder = getattr(orchestrator, "_generate_task_report", None)
    if effective_report_path is None and callable(report_builder):
        effective_report_path = report_builder(
            answer, status=status, error=error, receipt=effective_receipt,
        )
    if effective_report_path:
        effective_receipt["report_path"] = effective_report_path
    return result_type(
        status=status,
        answer=answer,
        workspace=str(workspace),
        error=error,
        diagnostics=effective_diagnostics,
        metadata=metadata or {},
        receipt=effective_receipt,
        report_path=effective_report_path,
    )

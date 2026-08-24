"""Minimal public projection of one canonical agent run."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agent.execution_incidents import CANONICAL_COMMIT_FAILED
from agent.llm.contracts import ModelProviderError
from agent.reporting.observation_evidence import (
    project_artifact_evidence,
    project_tool_observation,
    result_error_code,
)
from agent.reporting.operational_outcome import (
    has_canonical_commit_incident,
    local_failure_permitted,
    normalize_terminal_status,
    project_operational_outcome,
)
from agent.reporting.public_safety import sanitize_public_text
from agent.reporting.run_receipt_support import (
    executed_projection,
    execution_incidents,
    failure_layer_for_code,
    receipt_cause,
)


def _project_tool(entry: dict[str, Any]) -> dict[str, Any]:
    evidence = project_tool_observation(entry)
    tool = {
        "tool": evidence.tool,
        "invocation_id": evidence.invocation_id,
        "status": evidence.status,
        "executed": evidence.executed,
        "error_code": evidence.error_code,
    }
    return tool


def _canonical_public_status(
    state: Any,
    requested_status: str,
    *,
    task_failed: bool = False,
    cancelled: bool = False,
) -> str:
    last_result = getattr(state, "last_result", None) or {}
    normalized = normalize_terminal_status(
        explicit_status=requested_status,
        last_result_status=last_result.get("status") if isinstance(last_result, dict) else None,
        terminal_disposition=getattr(state, "terminal_disposition", None),
        task_failed=task_failed or bool(getattr(state, "_task_failed", False)),
        cancelled=cancelled or bool(getattr(state, "_cancelled", False)),
        local_failure_permitted=local_failure_permitted(state),
    )
    if normalized == "succeeded" and has_canonical_commit_incident(state):
        return "unverified"
    return normalized


def canonical_public_status(orchestrator: Any, requested_status: str) -> str:
    """Return the public status after applying the canonical run facts."""

    normalized = _canonical_public_status(
        orchestrator.agent_state,
        requested_status,
        task_failed=bool(getattr(orchestrator, "_task_failed", False)),
        cancelled=bool(getattr(orchestrator, "_cancelled", False)),
    )
    if normalized == "succeeded" and has_canonical_commit_incident(orchestrator.agent_state):
        return "unverified"
    return normalized


def build_run_receipt(
    workspace: str | Path,
    state: Any,
    status: str,
    error: str | None,
    *,
    failure_code: str | None = None,
    failure_layer: str | None = None,
) -> dict[str, Any]:
    status = _canonical_public_status(state, status)
    history = [
        item for item in (getattr(state, "tool_history", None) or [])
        if isinstance(item, dict)
    ]
    incidents = execution_incidents(state)
    tools: list[dict[str, Any]] = []
    proposed: set[str] = set()
    validation: dict[str, Any] = {"ran": False, "outcome": None}
    rollback: dict[str, Any] = {"occurred": False, "outcome": None}
    effects: list[bool] = []
    for entry in history:
        tool = _project_tool(entry)
        tools.append(tool)
        raw_result = entry.get("result")
        artifact = project_artifact_evidence(raw_result)
        effect = tool["executed"]
        if isinstance(effect, bool):
            effects.append(effect)
        proposed.update(artifact.affected_files)
        if artifact.validation_status is not None:
            validation["ran"] = True
            validation["outcome"] = artifact.validation_status
        if artifact.rollback_occurred:
            rollback["occurred"] = True
            rollback["outcome"] = "restored"
    if any(item.get("rollback_occurred") is True for item in incidents):
        rollback["occurred"] = True
        rollback["outcome"] = "restored"

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
    raw_code = result_error_code(last_result) if isinstance(last_result, dict) else None
    if raw_code is None and incidents:
        raw_code = CANONICAL_COMMIT_FAILED
    code = failure_code or raw_code
    cause = receipt_cause(error, code, last_result, failure_layer)
    executed = executed_projection(effects, incidents)
    replan = {"occurred": True, "count": replan_count} if replan_count else None
    outcome = project_operational_outcome(
        state,
        terminal_status=status,
        task_failed=bool(getattr(state, "_task_failed", False)),
        cancelled=bool(getattr(state, "_cancelled", False)),
    )
    final_state = (
        "restored"
        if outcome.rollback_occurred
        else ("applied" if outcome.mutation_occurred else None)
    )
    return {
        "workspace": str(workspace),
        "tools": tools,
        "execution_incidents": incidents,
        "files_affected": list(outcome.files_affected),
        "proposed_files": sorted(proposed - set(outcome.files_affected)),
        "validation": validation,
        "rollback": rollback,
        "final_state": final_state,
        "mutation_occurred": outcome.mutation_occurred,
        "operational_outcome": outcome.to_dict(),
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
    observed_code = result_error_code(last) if isinstance(last, dict) else None
    code = failure_code or observed_code
    if error or code:
        observed_error = last.get("error") if isinstance(last, dict) else None
        diagnostics.append({
            "layer": failure_layer or failure_layer_for_code(code),
            "code": code or "RUN_FAILED",
            "message": sanitize_public_text(
                error or observed_error or "A execucao nao foi concluida."
            ),
            "executed": last.get("executed") if isinstance(last.get("executed"), bool) else None,
        })
    last_result = getattr(state, "last_result", None) or {}
    data = last_result.get("data") if isinstance(last_result, dict) else None
    raw = data.get("diagnostics") if isinstance(data, dict) else None
    for item in raw[:20] if isinstance(raw, (list, tuple)) else []:
        if isinstance(item, dict):
            projected = {
                key: item[key]
                for key in ("code", "message", "severity", "file_path", "source", "layer")
                if key in item
            }
            if "message" in projected:
                projected["message"] = sanitize_public_text(projected["message"])
            if projected:
                diagnostics.append(projected)
    return tuple(diagnostics)


def derive_status(orchestrator: Any) -> str:
    last_result = orchestrator.agent_state.last_result or {}
    return normalize_terminal_status(
        last_result_status=last_result.get("status"),
        terminal_disposition=getattr(
            orchestrator.agent_state, "terminal_disposition", None
        ),
        task_failed=bool(getattr(orchestrator, "_task_failed", False)),
        cancelled=bool(getattr(orchestrator, "_cancelled", False)),
    )


def derive_error(orchestrator: Any, status: str) -> str | None:
    if status == "succeeded":
        return None
    last_result = orchestrator.agent_state.last_result or {}
    return str(last_result.get("error")) if last_result.get("error") else None


def public_exception_message(exc: BaseException) -> str:
    if isinstance(exc, ModelProviderError):
        return str(exc.public_message)
    text = str(exc).strip()
    safe = sanitize_public_text(text)
    return f"{type(exc).__name__}: {safe}" if safe else type(exc).__name__


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
    effective_status = canonical_public_status(orchestrator, status)
    failure_code = getattr(orchestrator, "_last_failure_code", None)
    failure_layer = getattr(orchestrator, "_last_failure_layer", None)
    effective_receipt = build_run_receipt(
        workspace, orchestrator.agent_state, effective_status, error,
        failure_code=failure_code, failure_layer=failure_layer,
    )
    effective_receipt["status"] = effective_status
    outcome = project_operational_outcome(
        orchestrator.agent_state,
        terminal_status=effective_status,
        task_failed=bool(getattr(orchestrator, "_task_failed", False)),
        cancelled=bool(getattr(orchestrator, "_cancelled", False)),
    )
    effective_receipt["operational_outcome"] = outcome.to_dict()
    from agent.final_response import compose_operational_answer
    public_answer = compose_operational_answer(
        outcome, answer,
        getattr(orchestrator.agent_state, "tool_history", ()),
        getattr(orchestrator, "tool_registry", None),
    )
    effective_diagnostics = diagnostics or build_run_diagnostics(
        orchestrator.agent_state, error, failure_code=failure_code, failure_layer=failure_layer,
    )
    effective_report_path = report_path
    report_builder = getattr(orchestrator, "_generate_task_report", None)
    if effective_report_path is None and callable(report_builder):
        effective_report_path = report_builder(
            public_answer, status=effective_status, error=error, receipt=effective_receipt,
        )
    if effective_report_path:
        effective_receipt["report_path"] = effective_report_path
    return result_type(
        status=effective_status,
        answer=public_answer,
        workspace=str(workspace),
        error=error,
        diagnostics=effective_diagnostics,
        metadata=metadata or {},
        receipt=effective_receipt,
        report_path=effective_report_path,
    )

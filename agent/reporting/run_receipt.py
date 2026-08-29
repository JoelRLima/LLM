"""Minimal public projection of one canonical agent run."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from agent.llm.contracts import ModelProviderError
from agent.reporting.observation_evidence import result_error_code
from agent.reporting.public_safety import sanitize_public_text
from agent.reporting.run_projection_facts import thaw_projection
from agent.reporting.run_receipt_builder import build_run_receipt
from agent.reporting.run_receipt_support import (
    failure_layer_for_code,
    metrics_for_orchestrator,
)
from agent.reporting.run_snapshot import CanonicalRunSnapshot
from agent.runtime.operational_outcome import (
    has_canonical_commit_incident,
    local_failure_permitted,
    normalize_terminal_status,
    project_operational_outcome,
)


def _canonical_public_status(
    state: Any,
    requested_status: str,
    *,
    task_failed: bool = False,
    cancelled: bool = False,
    snapshot: Any = None,
) -> str:
    if snapshot is not None:
        return str(snapshot.status)
    last_result = getattr(state, "last_result", None) or {}
    if snapshot is None:
        normalized = normalize_terminal_status(
            explicit_status=requested_status,
            last_result_status=last_result.get("status") if isinstance(last_result, Mapping) else None,
            terminal_disposition=getattr(state, "terminal_disposition", None),
            task_failed=task_failed or bool(getattr(state, "_task_failed", False)),
            cancelled=cancelled or bool(getattr(state, "_cancelled", False)),
            local_failure_permitted=local_failure_permitted(state),
        )
    else:
        normalized = str(snapshot.status)
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


_metrics_for_orchestrator = metrics_for_orchestrator


def build_run_diagnostics(
    state: Any,
    error: str | None,
    *,
    failure_code: str | None = None,
    failure_layer: str | None = None,
) -> tuple[dict[str, Any], ...]:
    diagnostics: list[dict[str, Any]] = []
    last = getattr(state, "last_result", None) or {}
    observed_code = result_error_code(last) if isinstance(last, Mapping) else None
    code = failure_code or observed_code
    if error or code:
        observed_error = last.get("error") if isinstance(last, Mapping) else None
        diagnostics.append({
            "layer": failure_layer or failure_layer_for_code(code),
            "code": code or "RUN_FAILED",
            "message": sanitize_public_text(
                error or observed_error or "A execucao nao foi concluida."
            ),
            "executed": last.get("executed") if isinstance(last.get("executed"), bool) else None,
        })
    last_result = getattr(state, "last_result", None) or {}
    data = last_result.get("data") if isinstance(last_result, Mapping) else None
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


def derive_status(orchestrator: Any, *, snapshot: Any = None) -> str:
    if snapshot is not None:
        return str(snapshot.status)
    last_result = orchestrator.agent_state.last_result or {}
    if snapshot is None:
        return normalize_terminal_status(
            last_result_status=last_result.get("status"),
            terminal_disposition=getattr(
                orchestrator.agent_state, "terminal_disposition", None
            ),
            task_failed=bool(getattr(orchestrator, "_task_failed", False)),
            cancelled=bool(getattr(orchestrator, "_cancelled", False)),
        )
    return str(snapshot.status)


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
    snapshot: CanonicalRunSnapshot | None = None,
) -> Any:
    if snapshot is None:
        effective_status = canonical_public_status(orchestrator, status)
        failure_code = getattr(orchestrator, "_last_failure_code", None)
        failure_layer = getattr(orchestrator, "_last_failure_layer", None)
        effective_receipt = build_run_receipt(
            workspace, orchestrator.agent_state, effective_status, error,
            failure_code=failure_code, failure_layer=failure_layer,
            metrics=_metrics_for_orchestrator(orchestrator),
        )
        effective_receipt["status"] = effective_status
        outcome = project_operational_outcome(
            orchestrator.agent_state,
            terminal_status=effective_status,
            task_failed=bool(getattr(orchestrator, "_task_failed", False)),
            cancelled=bool(getattr(orchestrator, "_cancelled", False)),
        )
        effective_receipt["operational_outcome"] = outcome.to_dict()
    else:
        effective_status = snapshot.status
        failure = snapshot.failure_fact
        failure_code = failure.code if failure is not None else getattr(orchestrator, "_last_failure_code", None)
        failure_layer = failure.layer.value if failure is not None else getattr(orchestrator, "_last_failure_layer", None)
        effective_receipt = build_run_receipt(
            workspace,
            orchestrator.agent_state,
            effective_status,
            error,
            failure_code=failure_code,
            failure_layer=failure_layer,
            snapshot=snapshot,
        )
        outcome = snapshot.operational_outcome
    from agent.final_response import compose_operational_answer
    answer_history = (
        [thaw_projection(item) for item in snapshot.projection_facts.invocation_evidence]
        if snapshot is not None
        else getattr(orchestrator.agent_state, "tool_history", ())
    )
    public_answer = compose_operational_answer(
        outcome,
        answer,
        answer_history,
        getattr(orchestrator, "tool_registry", None),
    )
    snapshot_diagnostics = (
        tuple(item for item in snapshot.to_dict()["diagnostics"] if isinstance(item, dict))
        if snapshot is not None
        else ()
    )
    effective_diagnostics = diagnostics or snapshot_diagnostics or build_run_diagnostics(
        orchestrator.agent_state,
        error,
        failure_code=failure_code,
        failure_layer=failure_layer,
    )
    effective_report_path = report_path
    report_builder = getattr(orchestrator, "_generate_task_report", None)
    if effective_report_path is None and callable(report_builder):
        effective_report_path = report_builder(
            public_answer,
            status=effective_status,
            error=error,
            receipt=effective_receipt,
            snapshot=snapshot,
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
        snapshot=snapshot,
    )

"""Compatibility receipt builder fed by canonical runtime facts."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from agent.execution_incidents import CANONICAL_COMMIT_FAILED
from agent.reporting.observation_evidence import project_tool_observation, result_error_code
from agent.reporting.run_projection_facts import thaw_projection
from agent.reporting.run_receipt_support import (
    executed_projection,
    execution_incidents,
    receipt_cause,
)
from agent.reporting.run_snapshot import CanonicalRunSnapshot
from agent.runtime.mutation_evidence import project_mutation_evidence


def _project_tool(entry: dict[str, Any]) -> dict[str, Any]:
    evidence = project_tool_observation(entry)
    return {
        "tool": evidence.tool,
        "invocation_id": evidence.invocation_id,
        "status": evidence.status,
        "executed": evidence.executed,
        "error_code": evidence.error_code,
    }


def _history_projection(
    state: Any,
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    set[str],
    dict[str, Any],
    dict[str, Any],
    list[bool],
]:
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
        artifact = project_mutation_evidence(raw_result)
        effect = tool["executed"]
        if isinstance(effect, bool):
            effects.append(effect)
        proposed.update(artifact.affected_files)
        if artifact.validation_status is not None:
            validation["ran"] = True
            validation["outcome"] = artifact.validation_status
        if artifact.rollback_occurred:
            rollback["occurred"] = True
            rollback["outcome"] = "restored" if artifact.final_state == "restored" else "unknown"
    if any(item.get("rollback_occurred") is True for item in incidents):
        rollback["occurred"] = True
        rollback["outcome"] = "restored"
    return history, incidents, tools, proposed, validation, rollback, effects


def build_run_receipt(
    workspace: str | Path,
    state: Any,
    status: str,
    error: str | None,
    *,
    failure_code: str | None = None,
    failure_layer: str | None = None,
    metrics: Mapping[str, Any] | None = None,
    snapshot: CanonicalRunSnapshot | None = None,
) -> dict[str, Any]:
    if snapshot is not None:
        status = snapshot.status
        metrics = snapshot.metrics.to_dict()
        failure = snapshot.failure_fact
        failure_code = failure.code if failure is not None else None
        failure_layer = failure.layer.value if failure is not None else None
        error = failure.message if failure is not None else None
        facts = snapshot.projection_facts
        incidents = [thaw_projection(item) for item in facts.incidents]
        tools = [thaw_projection(item) for item in facts.tools]
        proposed = set(facts.proposed_files)
        validation = thaw_projection(facts.validation)
        rollback = thaw_projection(facts.rollback)
        executed = facts.executed
        repair_count = facts.repair_count
        replan_count = facts.replan_count
        last_result: dict[str, Any] = {}
        raw_code = None
    else:
        history, incidents, tools, proposed, validation, rollback, effects = _history_projection(state)
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
        raw_code = result_error_code(last_result) if isinstance(last_result, Mapping) else None
        executed = executed_projection(effects, incidents)
    if raw_code is None and incidents:
        raw_code = CANONICAL_COMMIT_FAILED
    code = failure_code or raw_code
    cause = receipt_cause(error, code, last_result, failure_layer)
    replan = {"occurred": True, "count": replan_count} if replan_count else None
    outcome = (
        snapshot.operational_outcome
        if snapshot is not None
        else _legacy_outcome(state, status)
    )
    rollback["occurred"] = bool(outcome.rollback_occurred)
    rollback["outcome"] = (
        "restored"
        if outcome.rollback_succeeded is True
        else "unknown"
        if outcome.rollback_occurred
        else None
    )
    status = outcome.terminal_status
    receipt = {
        "workspace": str(workspace),
        "tools": tools,
        "execution_incidents": incidents,
        "files_affected": list(outcome.files_affected),
        "proposed_files": sorted(proposed - set(outcome.files_affected)),
        "validation": validation,
        "rollback": rollback,
        "final_state": outcome.final_state,
        "mutation_occurred": outcome.mutation_occurred,
        "operational_outcome": outcome.to_dict(),
        "repair": {"occurred": repair_count > 0, "count": repair_count},
        "replan": replan,
        "error": cause,
        "executed": executed,
        "status": status,
    }
    if snapshot is not None:
        receipt["correlation"] = snapshot.correlation.as_dict()
        receipt["run_id"] = snapshot.correlation.run_id
        receipt["root_task_id"] = snapshot.correlation.root_task_id
        receipt["task_id"] = snapshot.correlation.task_id
        if snapshot.failure_fact is not None:
            receipt["failure_fact"] = snapshot.failure_fact.to_dict()
    if metrics is not None:
        receipt["metrics"] = dict(metrics)
    return receipt


def _legacy_outcome(state: Any, status: str, *, snapshot: Any = None) -> Any:
    from agent.runtime.operational_outcome import project_operational_outcome

    if snapshot is None:
        return project_operational_outcome(
            state,
            terminal_status=status,
            task_failed=bool(getattr(state, "_task_failed", False)),
            cancelled=bool(getattr(state, "_cancelled", False)),
        )
    return snapshot


__all__ = ["build_run_receipt"]

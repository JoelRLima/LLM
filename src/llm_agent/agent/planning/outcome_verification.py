"""Fresh filesystem observations for checks admitted in an immutable Contract."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from llm_agent.agent.planning.outcome_definition import active_outcome_definition
from llm_agent.agent.planning.plan_coverage_models import PlanCoverageError
from llm_agent.agent.planning.requirement_verification import verify_requirement_checks
from llm_agent.agent.task_definition.errors import TaskDefinitionError
from llm_agent.agent.task_definition.models import TaskDefinitionRef
from llm_agent.agent.task_definition.verification_model import VerificationCheck


def requirement_verification_document(orchestrator: Any) -> dict[str, Any]:
    """Read current workspace facts; no cached observation can authorize success."""
    state = getattr(orchestrator, "agent_state", orchestrator)
    if not isinstance(getattr(state, "task_definition_ref", None), TaskDefinitionRef):
        return {}
    try:
        context, reference = active_outcome_definition(orchestrator)
        checks = tuple(
            VerificationCheck.from_dict(item)
            for item in context.structured["contract"].get("verification_checks", ())
        )
        if not checks:
            return {}
        root = getattr(orchestrator, "workspace_root", None)
        if root is None:
            return _unavailable("workspace_unavailable")
        observations = verify_requirement_checks(checks, Path(root))
        addresses = {
            (item.source, item.index): item.requirement_id
            for item in context.requirement_references
        }
        rows = []
        for check, observation in zip(checks, observations, strict=True):
            requirement_id = addresses[(f"contract.{check.field}", check.index)]
            rows.append({
                **asdict(observation), "requirement_id": requirement_id,
                "kind": check.kind, "path": check.path,
                "required_fields": list(check.required_fields),
            })
    except (PlanCoverageError, TaskDefinitionError, OSError, ValueError, KeyError) as exc:
        return _unavailable(type(exc).__name__)
    return {
        "schema_version": 1, "status": "available", "definition": reference.to_dict(),
        "verification_scope": "declared_checks_only", "checks": rows,
        "overall_status": combined_verification_status([row["status"] for row in rows]),
    }


def combined_verification_status(statuses: list[str]) -> str:
    """Combine declared checks without interpreting the surrounding free text."""
    if not statuses:
        return "unverified"
    if "violated" in statuses:
        return "violated"
    return "satisfied" if all(value == "satisfied" for value in statuses) else "inconclusive"


def planning_verification_summary(orchestrator: Any) -> dict[str, Any]:
    """Expose bounded pending-check facts at a model planning boundary."""
    document = requirement_verification_document(orchestrator)
    if not document:
        return {}
    rows = document.get("checks", ())
    return {
        "status": document["status"], "overall_status": document["overall_status"],
        "checks": [
            {key: row[key] for key in ("check_id", "requirement_id", "status", "reason_code")}
            for row in rows[:16]
        ],
        "total_checks": len(rows), "omitted_checks": max(0, len(rows) - 16),
    }


def attach_requirement_verification(document: dict[str, Any], verification: dict[str, Any]) -> None:
    """Attach observed conditions to coverage while preserving unmapped plan claims."""
    if not verification:
        return
    document["requirement_verification"] = verification
    rows = verification.get("checks", ())
    statuses: dict[str, list[str]] = {}
    for row in rows:
        statuses.setdefault(row["requirement_id"], []).append(row["status"])
    satisfied = set()
    for requirement_id, values in statuses.items():
        if combined_verification_status(values) == "satisfied":
            satisfied.add(requirement_id)
    for requirement in document.get("requirements", ()):
        values = statuses.get(requirement["requirement_id"], [])
        if values:
            requirement["verification_status"] = combined_verification_status(values)
            requirement["verification_scope"] = "declared_checks_only"
            requirement["verification_check_ids"] = [
                row["check_id"] for row in rows if row["requirement_id"] == requirement["requirement_id"]
            ]
    if verification.get("status") != "available":
        return
    # Calculate from the full catalogue rather than an already truncated ID list.
    total = document.get("total_requirements", 0) - len(satisfied)
    unresolved = [item for item in document.get("unresolved_requirement_ids", ()) if item not in satisfied]
    document["unresolved_requirement_ids"] = unresolved
    document["total_unresolved"] = total
    document["omitted_unresolved"] = max(0, total - len(unresolved))


def _unavailable(reason_code: str) -> dict[str, Any]:
    return {
        "schema_version": 1, "status": "unavailable", "overall_status": "inconclusive",
        "reason_code": reason_code, "checks": [],
    }

"""Runtime adapters for plan associations; semantic authority stays unchanged."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from typing import Any

from llm_agent.agent.planning.outcome_definition import active_outcome_definition as _definition
from llm_agent.agent.planning.outcome_verification import (
    attach_requirement_verification,
    planning_verification_summary,
    requirement_verification_document,
)
from llm_agent.agent.planning.plan_coverage import (
    PlanCoverageError,
    project_plan_coverage,
    requirement_catalog,
    validate_plan_links,
)
from llm_agent.agent.planning.plan_model import Plan, PlanModelError
from llm_agent.agent.task_definition.errors import TaskDefinitionError
from llm_agent.agent.task_definition.models import TaskDefinitionRef

MAX_CATALOG_CHARS = 8_000
MAX_CATALOG_ENTRIES = 32


def has_plan_links(plan: Plan | Sequence[Mapping[str, Any]]) -> bool:
    if not isinstance(plan, (Plan, Sequence)) or isinstance(plan, (str, bytes)):
        return False
    steps = plan.steps if isinstance(plan, Plan) else plan
    return any(
        ("requirement_ids" in step or "obligation_ids" in step)
        if isinstance(step, Mapping)
        else getattr(step, "requirement_ids", None) is not None or getattr(step, "obligation_ids", None) is not None
        for step in steps
    )


def validate_outcome_plan(orchestrator: Any, plan: Plan | Sequence[Mapping[str, Any]]) -> None:
    """Validate claims before admission/recovery can cause a tool side effect."""
    if not has_plan_links(plan):
        return
    canonical = plan if isinstance(plan, Plan) else Plan.from_raw(plan)
    needs_definition = any(getattr(step, "requirement_ids", None) for step in canonical.steps)
    materialization, reference = _definition(orchestrator) if needs_definition else (None, None)
    validate_plan_links(canonical, materialization, reference, orchestrator.agent_state.task_semantics)


def admit_outcome_links(gateway: Any, plan: Plan | Sequence[Mapping[str, Any]]) -> bool:
    try:
        validate_outcome_plan(gateway.orchestrator, plan)
    except (PlanCoverageError, PlanModelError, TaskDefinitionError, OSError) as exc:
        gateway._abort("PLAN_REQUIREMENT_LINK_INVALID", [str(exc)])
        return False
    return True


def plan_coverage_document(orchestrator: Any) -> dict[str, Any]:
    """Produce inspection facts without changing the canonical run outcome."""
    state = getattr(orchestrator, "agent_state", orchestrator)
    if not isinstance(getattr(state, "task_definition_ref", None), TaskDefinitionRef):
        return {}
    try:
        materialization, reference = _definition(orchestrator)
        projection = project_plan_coverage(
            state.plan, materialization, reference, state.task_semantics, state,
        )
    except (PlanCoverageError, TaskDefinitionError, OSError) as exc:
        return {"schema_version": 1, "status": "unavailable", "reason_code": type(exc).__name__}
    document = {
        "schema_version": 1,
        "status": "available",
        "definition": reference.to_dict(),
        "associations_are_claims": True,
        "truncated": projection.truncated,
        **asdict(projection),
    }
    attach_requirement_verification(document, requirement_verification_document(orchestrator))
    return document


def outcome_planning_context(orchestrator: Any) -> str:
    """Provide a bounded catalogue only at planner decision boundaries."""
    if not isinstance(getattr(orchestrator.agent_state, "task_definition_ref", None), TaskDefinitionRef):
        return ""
    try:
        materialization, reference = _definition(orchestrator)
        catalog = requirement_catalog(materialization, reference, limit=MAX_CATALOG_ENTRIES)
    except (PlanCoverageError, TaskDefinitionError, OSError):
        return ""
    entries = [_catalog_row(item, orchestrator.agent_state.plan) for item in catalog.entries]
    semantics = orchestrator.agent_state.task_semantics
    obligations = [
        {"id": item.id, "kind": item.kind, "description": item.description[:160]}
        for item in semantics.obligations
    ]
    verification = planning_verification_summary(orchestrator)
    total_obligations = len(obligations)
    payload = _catalog_payload(catalog, entries, obligations, total_obligations, verification)
    while len(json.dumps(payload, ensure_ascii=False, separators=(",", ":"))) > MAX_CATALOG_CHARS:
        if entries:
            entries.pop()
        elif obligations:
            obligations.pop()
        else:
            break
        payload = _catalog_payload(catalog, entries, obligations, total_obligations, verification)
    return "\nACTIVE TASK REQUIREMENT CATALOGUE:\n" + json.dumps(
        payload, ensure_ascii=False, separators=(",", ":")
    ) + (
        "\nAssocie cada etapa aos IDs pertinentes usando requirement_ids e, quando houver, obligation_ids. "
        "Use apenas IDs deste catálogo ativo. Vínculos são intenções de cobertura, não evidência. "
        "Uma etapa executada não comprova automaticamente um critério textual. Requisitos omitidos "
        "ou sem vínculo não podem ser apresentados como cobertos ou verificados.\n"
        "Quando uma condição estruturada estiver violated ou inconclusive, proponha apenas "
        "etapas de correção permitidas pelo objetivo e pelas permissões atuais. A decisão complete "
        "não dispensa verificações. Não altere o Contract nem seus critérios para obter sucesso.\n"
    )


def _catalog_payload(
    catalog: Any,
    entries: list[dict[str, Any]],
    obligations: list[dict[str, str]],
    total_obligations: int,
    verification: dict[str, Any],
) -> dict[str, Any]:
    return {
        "requirements": entries,
        "total_requirements": catalog.total,
        "omitted_requirements": catalog.total - len(entries),
        "visible_unmapped_requirement_ids": [item["id"] for item in entries if not item["step_ids"]],
        "admitted_obligations": obligations,
        "total_obligations": total_obligations,
        "omitted_obligations": total_obligations - len(obligations),
        "requirement_verification": verification,
    }


def _catalog_row(item: Any, plan: Plan) -> dict[str, Any]:
    steps = [step.step_id for step in plan if item.requirement_id in (getattr(step, "requirement_ids", None) or ())]
    return {
        "id": item.requirement_id,
        "path": item.path,
        "text": item.text[:240],
        "text_truncated": len(item.text) > 240,
        "step_ids": steps[:8],
        "omitted_steps": max(0, len(steps) - 8),
    }


__all__ = ["admit_outcome_links", "outcome_planning_context", "plan_coverage_document", "validate_outcome_plan"]

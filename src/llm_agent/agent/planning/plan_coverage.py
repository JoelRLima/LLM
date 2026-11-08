"""Read-only links between definition requirements, plan steps, and evidence."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Mapping, Protocol

from llm_agent.agent.contracts import ToolHistoryEntry
from llm_agent.agent.execution_state import StepExecutionRecord, StepStatus
from llm_agent.agent.planning.plan_coverage_models import (
    MAX_COVERAGE_ITEMS,
    MAX_REQUIREMENT_TEXT,
    PlanCoverageError,
    PlanCoverageProjection,
    RequirementCatalog,
    RequirementCatalogEntry,
    RequirementCoverage,
    StepCoverage,
)
from llm_agent.agent.planning.plan_model import Plan, PlanStep
from llm_agent.agent.planning.task_semantics import TaskSemantics
from llm_agent.agent.task_definition.models import TaskDefinitionRef
from llm_agent.agent.task_definition.requirement_refs import RequirementReference
from llm_agent.agent.task_definition.resolver import TaskContextMaterialization


class CoverageState(Protocol):
    plan_identity: str | None
    step_records: Mapping[str, StepExecutionRecord]
    tool_history: Sequence[ToolHistoryEntry]


def validate_plan_links(
    plan: Plan,
    materialization: TaskContextMaterialization | None,
    active_reference: TaskDefinitionRef | None,
    semantics: TaskSemantics,
) -> None:
    """Reject stale/unknown IDs without changing definition or semantic authority."""
    if not isinstance(plan, Plan) or not isinstance(semantics, TaskSemantics):
        raise PlanCoverageError("plan e TaskSemantics tipados são obrigatórios")
    linked_requirements = tuple(
        requirement_id for step in plan.steps for requirement_id in _ids(step, "requirement_ids")
    )
    if linked_requirements:
        if materialization is None or active_reference is None:
            raise PlanCoverageError("links de requirement exigem catálogo e TaskDefinitionRef ativa")
        _validate_active_definition(materialization, active_reference)
    elif materialization is not None and active_reference is not None:
        _validate_active_definition(materialization, active_reference)
    requirements = (
        {item.requirement_id for item in materialization.requirement_references}
        if materialization is not None else set()
    )
    obligations = {item.id for item in semantics.obligations}
    for step in plan.steps:
        for requirement_id in _ids(step, "requirement_ids"):
            if requirement_id not in requirements:
                raise PlanCoverageError(f"requirement_id desconhecido ou stale: {requirement_id}")
        for obligation_id in _ids(step, "obligation_ids"):
            if obligation_id not in obligations:
                raise PlanCoverageError(f"obligation_id não admitido: {obligation_id}")


def unresolved_requirement_ids(
    plan: Plan,
    materialization: TaskContextMaterialization,
    active_reference: TaskDefinitionRef,
    semantics: TaskSemantics,
) -> tuple[str, ...]:
    """Return the complete set lacking a canonical textual verifier, even when linked."""
    validate_plan_links(plan, materialization, active_reference, semantics)
    return tuple(item.requirement_id for item in materialization.requirement_references)


def requirement_catalog(
    materialization: TaskContextMaterialization,
    active_reference: TaskDefinitionRef,
    *,
    limit: int = 64,
) -> RequirementCatalog:
    """Return bounded planner-facing requirement IDs, paths, and text."""
    _validate_active_definition(materialization, active_reference)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 0 <= limit <= MAX_COVERAGE_ITEMS:
        raise ValueError("limit deve estar entre 0 e 64")
    references = materialization.requirement_references
    texts = _requirement_texts(materialization, references)
    entries = tuple(
        RequirementCatalogEntry(
            requirement_id=item.requirement_id,
            field=item.source.split(".", 1)[1],
            path=_requirement_path(item),
            text=texts[item.requirement_id],
        )
        for item in references[:limit]
    )
    return RequirementCatalog(entries, len(references), max(0, len(references) - len(entries)))


def project_plan_coverage(
    plan: Plan,
    materialization: TaskContextMaterialization,
    active_reference: TaskDefinitionRef,
    semantics: TaskSemantics,
    state: CoverageState,
    *,
    limit: int = 64,
) -> PlanCoverageProjection:
    """Project all associations and canonical evidence, then bound returned rows."""
    validate_plan_links(plan, materialization, active_reference, semantics)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 0 <= limit <= MAX_COVERAGE_ITEMS:
        raise ValueError("limit deve estar entre 0 e 64")
    catalog = materialization.requirement_references
    by_id = _requirement_texts(materialization, catalog)
    linked: dict[str, list[PlanStep]] = {item.requirement_id: [] for item in catalog}
    for step in plan.steps:
        for requirement_id in _ids(step, "requirement_ids"):
            linked[requirement_id].append(step)
    full: list[RequirementCoverage] = []
    unmapped: list[str] = []
    for reference in catalog:
        steps = linked[reference.requirement_id]
        if not steps:
            unmapped.append(reference.requirement_id)
        projected = tuple(_step_coverage(step, state, semantics) for step in steps[:limit])
        text = by_id[reference.requirement_id]
        full.append(RequirementCoverage(
            requirement_id=reference.requirement_id,
            source=reference.source,
            field=reference.source.split(".", 1)[1],
            index=reference.index,
            path=_requirement_path(reference),
            phase_id=reference.phase_id,
            text=text[:MAX_REQUIREMENT_TEXT],
            status="unmapped" if not steps else "linked",
            verification_status="unverified",
            text_truncated=len(text) > MAX_REQUIREMENT_TEXT,
            steps=projected,
            step_count=len(steps),
            omitted_steps=max(0, len(steps) - len(projected)),
        ))
    visible = tuple(full[:limit])
    unresolved = tuple(item.requirement_id for item in catalog)
    return PlanCoverageProjection(
        requirements=visible,
        unresolved_requirement_ids=unresolved[:limit],
        unmapped_requirement_ids=tuple(unmapped[:limit]),
        total_requirements=len(full),
        omitted_requirements=max(0, len(full) - len(visible)),
        total_unresolved=len(unresolved),
        omitted_unresolved=max(0, len(unresolved) - limit),
        total_unmapped=len(unmapped),
        omitted_unmapped=max(0, len(unmapped) - limit),
    )


def _validate_active_definition(
    context: TaskContextMaterialization, reference: TaskDefinitionRef
) -> None:
    matches = (
        isinstance(context, TaskContextMaterialization)
        and isinstance(reference, TaskDefinitionRef)
        and reference.is_complete
        and context.task_id == reference.task_id
        and context.contract_version == reference.contract_version
        and context.contract_digest == reference.contract_digest
        and context.spec_version == reference.spec_version
        and context.spec_digest == reference.spec_digest
        and context.phase_id == reference.active_phase_id
    )
    if not matches:
        raise PlanCoverageError("catálogo não corresponde à TaskDefinitionRef ativa exata")


def _ids(step: PlanStep, name: str) -> tuple[str, ...]:
    value = getattr(step, name, None)
    return value or ()


def _requirement_texts(
    context: TaskContextMaterialization, references: tuple[RequirementReference, ...]
) -> dict[str, str]:
    authority = context.structured
    result: dict[str, str] = {}
    for reference in references:
        if reference.source.startswith("contract."):
            owner = authority["contract"]
        elif reference.source.startswith("spec."):
            owner = authority["spec"]
        else:
            owner = _phase_owner(authority, reference.phase_id)
        field = reference.source.split(".", 1)[1]
        result[reference.requirement_id] = str(owner[field][reference.index])
    return result


def _requirement_path(reference: RequirementReference) -> str:
    phase = f"phase.{reference.phase_id}." if reference.phase_id is not None else ""
    field = reference.source.split(".", 1)[1]
    return f"{phase}{field}[{reference.index}]"


def _phase_owner(authority: Mapping[str, Any], phase_id: str | None) -> Mapping[str, Any]:
    candidates = list(authority["spec"].get("phases", ()))
    candidates.extend((authority.get("selected_phase"), *authority.get("dependency_facts", ())))
    for phase in candidates:
        if isinstance(phase, Mapping) and phase.get("phase_id") == phase_id:
            return phase
    raise PlanCoverageError(f"fase ausente no catálogo ativo: {phase_id}")


def _step_coverage(step: PlanStep, state: CoverageState, semantics: TaskSemantics) -> StepCoverage:
    record = state.step_records.get(step.step_id)
    status = _status_value(record.status if record is not None else StepStatus.PENDING)
    plan_id = state.plan_identity
    history = state.tool_history
    entries = [
        (index, entry)
        for index, entry in enumerate(history, start=1)
        if entry.get("step_id") == step.step_id
        and entry.get("plan_id") == plan_id
    ]
    if record is None or status == StepStatus.PENDING.value:
        latest = entries[-1][1] if entries else None
        status = _history_status(latest) if latest is not None else status
    invocation_refs = tuple(
        value for _, entry in entries
        if isinstance((value := entry.get("invocation_id")), str) and value
    )
    evidence_refs = tuple(index for index, _entry in entries)
    obligation_ids = _ids(step, "obligation_ids")
    canonical = tuple(
        (item, semantics.obligation_status(item).value) for item in obligation_ids
    )
    semantic_refs = tuple(
        ref for item in obligation_ids for ref in semantics.obligation_evidence(item)
    )
    visible_invocations = invocation_refs[:MAX_COVERAGE_ITEMS]
    visible_evidence = evidence_refs[:MAX_COVERAGE_ITEMS]
    visible_canonical = semantic_refs[:MAX_COVERAGE_ITEMS]
    return StepCoverage(
        step_id=step.step_id,
        step_status=status,
        invocation_refs=visible_invocations,
        evidence_refs=visible_evidence,
        canonical_evidence_refs=visible_canonical,
        obligation_ids=obligation_ids,
        canonical_statuses=canonical,
        total_invocation_refs=len(invocation_refs),
        omitted_invocation_refs=max(0, len(invocation_refs) - len(visible_invocations)),
        total_evidence_refs=len(evidence_refs),
        omitted_evidence_refs=max(0, len(evidence_refs) - len(visible_evidence)),
        total_canonical_evidence_refs=len(semantic_refs),
        omitted_canonical_evidence_refs=max(0, len(semantic_refs) - len(visible_canonical)),
    )


def _history_status(entry: ToolHistoryEntry) -> str:
    value = entry.get("status")
    if isinstance(value, str) and value:
        return value
    result = entry.get("result")
    ok = result.ok if result is not None else None
    if ok is True:
        return "completed"
    if ok is False:
        return "failed"
    return "unverified"


def _status_value(value: StepStatus | str) -> str:
    return value.value if isinstance(value, StepStatus) else value


__all__ = [
    "PlanCoverageError",
    "PlanCoverageProjection",
    "MAX_COVERAGE_ITEMS",
    "MAX_REQUIREMENT_TEXT",
    "RequirementCatalog",
    "RequirementCatalogEntry",
    "RequirementCoverage",
    "StepCoverage",
    "project_plan_coverage",
    "requirement_catalog",
    "unresolved_requirement_ids",
    "validate_plan_links",
]

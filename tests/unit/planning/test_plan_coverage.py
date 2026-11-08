from __future__ import annotations

from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest

from llm_agent.agent.execution_state import StepExecutionRecord, StepStatus
from llm_agent.agent.planning.plan_coverage import (
    PlanCoverageError,
    project_plan_coverage,
    requirement_catalog,
    unresolved_requirement_ids,
    validate_plan_links,
)
from llm_agent.agent.planning.plan_model import Plan, ToolPlanStep
from llm_agent.agent.planning.task_semantics import TaskSemantics
from llm_agent.agent.planning.task_semantics_types import TaskIntent, TaskObligation
from llm_agent.agent.task_definition.models import TaskDefinitionRef
from llm_agent.agent.task_definition.resolver import TaskContextResolver
from tests.support.task_definition import make_contract, make_spec


def _definition(task_id: str = "coverage-task", *, acceptance: str = "checks pass"):
    contract = make_contract(task_id, "Repair task", requirements=("read input", "preserve output"))
    spec = make_spec(contract, global_acceptance=(acceptance,))
    context = TaskContextResolver().materialize(contract, spec)
    reference = TaskDefinitionRef(
        task_id=task_id,
        contract_version=contract.version,
        contract_digest=context.contract_digest,
        definition_state="complete",
        spec_version=spec.version,
        spec_digest=context.spec_digest,
    )
    return context, reference


def _semantics(evidence_count: int = 1) -> TaskSemantics:
    obligation = TaskObligation(
        id="obl-read", kind="read", description="Read source", target="input.txt"
    )
    semantics = TaskSemantics(TaskIntent("Read source"), (obligation,))
    for evidence_ref in range(77, 77 + evidence_count):
        observation = {
            "ok": True, "done": True, "status": "succeeded", "complete": True, "data": "contents"
        }
        semantics.register_observation(
            "file_reader",
            observation,
            evidence_ref=evidence_ref,
            args={"file_path": "input.txt"},
        )
        semantics.satisfy(
            obligation.id,
            evidence_ref=evidence_ref,
        )
    return semantics


def _step(step_id: str, *requirements: str, obligations: tuple[str, ...] | None = None):
    return ToolPlanStep(
        step_id,
        "read_file",
        {"path": "input.txt"},
        requirement_ids=tuple(requirements) if requirements else None,
        obligation_ids=obligations,
    )


def _state(*records, history=(), plan_id="plan-active"):
    return SimpleNamespace(
        plan_identity=plan_id,
        step_records={record.step_id: record for record in records},
        tool_history=list(history),
    )


def test_links_validate_exact_catalog_and_obligation_owner_without_mutation():
    context, reference = _definition()
    semantics = _semantics()
    requirement_id = context.requirement_references[0].requirement_id
    plan = Plan((_step("s1", requirement_id, obligations=("obl-read",)),))
    before = semantics.to_checkpoint_dict()

    validate_plan_links(plan, context, reference, semantics)

    assert semantics.to_checkpoint_dict() == before
    assert unresolved_requirement_ids(plan, context, reference, semantics) == tuple(
        item.requirement_id for item in context.requirement_references
    )


def test_unknown_cross_definition_requirement_and_stale_spec_are_rejected():
    first, first_ref = _definition("first-task")
    other, other_ref = _definition("other-task")
    semantics = _semantics()
    foreign_id = other.requirement_references[0].requirement_id

    with pytest.raises(PlanCoverageError, match="unknown|stale|desconhecido"):
        validate_plan_links(Plan((_step("foreign", foreign_id),)), first, first_ref, semantics)

    revised, _ = _definition("first-task", acceptance="updated checks pass")
    with pytest.raises(PlanCoverageError, match="exata"):
        validate_plan_links(Plan(()), revised, first_ref, semantics)
    assert other_ref.task_id == "other-task"


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (StepStatus.PENDING, "pending"),
        (StepStatus.RUNNING, "running"),
        (StepStatus.FAILED, "failed"),
        (StepStatus.SKIPPED, "skipped"),
        (StepStatus.UNVERIFIED, "unverified"),
        (StepStatus.CANCELLED, "cancelled"),
        (StepStatus.COMPLETED, "completed"),
    ],
)
def test_step_outcome_never_verifies_textual_criterion(status, expected):
    context, reference = _definition()
    semantics = _semantics()
    requirement_id = context.requirement_references[0].requirement_id
    step = _step("s1", requirement_id)
    projection = project_plan_coverage(
        Plan((step,)), context, reference, semantics,
        _state(StepExecutionRecord("s1", status=status)),
    )

    row = projection.requirements[0]
    assert row.status == "linked"
    assert row.verification_status == "unverified"
    assert row.steps[0].step_status == expected


def test_multiple_steps_and_history_keep_scope_and_canonical_evidence_separate():
    context, reference = _definition()
    semantics = _semantics()
    requirement_id = context.requirement_references[0].requirement_id
    plan = Plan((_step("s1", requirement_id, obligations=("obl-read",)), _step("s2", requirement_id)))
    history = [
        {"step_id": "s1", "plan_id": "plan-active", "invocation_id": "call-0"},
        {"step_id": "s1", "plan_id": "old", "invocation_id": "wrong-scope"},
        *({"step_id": f"other-{index}", "plan_id": "plan-active"} for index in range(6)),
        {"step_id": "s1", "plan_id": "plan-active", "invocation_id": "call-1"},
        {"step_id": "s1", "plan_id": "plan-active", "invocation_id": "call-2"},
    ]

    row = project_plan_coverage(
        plan, context, reference, semantics,
        _state(StepExecutionRecord("s1", StepStatus.COMPLETED), history=history),
    ).requirements[0]

    assert row.step_count == 2
    assert len(row.steps) == 2
    assert row.steps[0].invocation_refs == ("call-0", "call-1", "call-2")
    assert row.steps[0].evidence_refs == (1, 9, 10)
    assert row.steps[0].canonical_statuses == (("obl-read", "satisfied"),)
    assert row.steps[0].canonical_evidence_refs == (77,)
    assert row.steps[1].step_status == "pending"
    assert row.verification_status == "unverified"


def test_legacy_plan_is_valid_and_catalog_projection_is_bounded_and_immutable():
    context, reference = _definition()
    semantics = _semantics()
    catalog = requirement_catalog(context, reference, limit=1)
    projection = project_plan_coverage(Plan((_step("legacy"),)), context, reference, semantics, _state(), limit=1)

    assert catalog.total > len(catalog.entries) == 1
    assert catalog.omitted == catalog.total - 1
    assert projection.total_requirements == len(context.requirement_references)
    assert projection.omitted_requirements == projection.total_requirements - 1
    assert len(projection.unresolved_requirement_ids) == 1
    assert projection.total_unresolved == projection.total_requirements
    assert projection.omitted_unresolved == projection.total_unresolved - 1
    assert len(projection.unmapped_requirement_ids) == 1
    assert projection.total_unmapped == projection.total_requirements
    assert projection.omitted_unmapped == projection.total_unmapped - 1
    assert projection.truncated
    assert projection.requirements[0].status == "unmapped"
    assert projection.requirements[0].verification_status == "unverified"
    with pytest.raises(FrozenInstanceError):
        projection.requirements[0].status = "satisfied"


def test_unknown_obligation_link_is_rejected_and_legacy_without_catalog_is_valid():
    semantics = _semantics()
    legacy = Plan((_step("legacy"),))
    validate_plan_links(legacy, None, None, semantics)
    with pytest.raises(PlanCoverageError, match="não admitido"):
        validate_plan_links(Plan((_step("bad", obligations=("unknown",)),)), None, None, semantics)


def test_history_and_canonical_evidence_refs_are_bounded_after_full_calculation():
    context, reference = _definition()
    semantics = _semantics(evidence_count=70)
    requirement_id = context.requirement_references[0].requirement_id
    history = [
        {"step_id": "s1", "plan_id": "plan-active", "invocation_id": f"call-{index}"}
        for index in range(70)
    ]
    projection = project_plan_coverage(
        Plan((_step("s1", requirement_id, obligations=("obl-read",)),)),
        context,
        reference,
        semantics,
        _state(StepExecutionRecord("s1", StepStatus.COMPLETED), history=history),
    )
    step = projection.requirements[0].steps[0]

    assert len(step.invocation_refs) == 64
    assert step.total_invocation_refs == 70
    assert step.omitted_invocation_refs == 6
    assert len(step.evidence_refs) == 64
    assert step.total_evidence_refs == 70
    assert step.omitted_evidence_refs == 6
    assert len(step.canonical_evidence_refs) == 64
    assert step.total_canonical_evidence_refs == 70
    assert step.omitted_canonical_evidence_refs == 6
    assert projection.truncated


def test_requirement_projection_caps_text_and_marks_truncation():
    long_text = "x" * 600
    contract = make_contract("long-requirement", "Repair task", requirements=(long_text,))
    spec = make_spec(contract)
    context = TaskContextResolver().materialize(contract, spec)
    reference = TaskDefinitionRef(
        task_id=contract.task_id,
        contract_version=contract.version,
        contract_digest=context.contract_digest,
        definition_state="complete",
        spec_version=spec.version,
        spec_digest=context.spec_digest,
    )

    row = project_plan_coverage(Plan(), context, reference, _semantics(), _state()).requirements[0]

    assert len(row.text) == 500
    assert row.text_truncated

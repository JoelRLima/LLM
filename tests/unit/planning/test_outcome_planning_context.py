from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from llm_agent.agent.planning.outcome_planning import (
    MAX_CATALOG_CHARS,
    outcome_planning_context,
    plan_coverage_document,
    validate_outcome_plan,
)
from llm_agent.agent.planning.plan_coverage import PlanCoverageError
from llm_agent.agent.planning.plan_model import Plan
from llm_agent.agent.reporting.run_projection_facts import build_run_projection_facts
from llm_agent.agent.state import AgentState
from llm_agent.agent.task_definition.models import TaskDefinitionRef
from llm_agent.agent.task_definition.resolver import TaskContextResolver
from tests.support.task_definition import make_contract, make_spec


def _orchestrator(*, requirements=("Read the relevant source",)):
    contract = make_contract("outcome-context", "Read notes.txt", requirements=requirements)
    spec = make_spec(contract)
    context = TaskContextResolver().materialize(contract, spec, workspace_id="workspace")
    reference = TaskDefinitionRef(
        task_id=contract.task_id, contract_version=contract.version, contract_digest=contract.digest(),
        spec_version=spec.version, spec_digest=context.spec_digest, definition_state="complete",
    )
    state = AgentState()
    state.task_definition_ref = reference
    return SimpleNamespace(
        agent_state=state, task_context_resolver=SimpleNamespace(resolve=lambda _ref: context),
        workspace_paths=SimpleNamespace(workspace_id="workspace"),
    )


def _catalog(prompt):
    return json.loads(prompt.splitlines()[2])


def test_planner_distinguishes_unmapped_requirement_from_unverified_link():
    orchestrator = _orchestrator()
    original = _catalog(outcome_planning_context(orchestrator))
    requirement_id = original["requirements"][0]["id"]
    assert requirement_id in original["visible_unmapped_requirement_ids"]
    orchestrator.agent_state.set_plan(Plan.from_raw([{
        "tool": "file_reader", "args": {"file_path": "notes.txt"}, "_step_id": "read-source",
        "requirement_ids": [requirement_id],
    }]))

    current = _catalog(outcome_planning_context(orchestrator))
    coverage = plan_coverage_document(orchestrator)

    assert requirement_id not in current["visible_unmapped_requirement_ids"]
    assert current["requirements"][0]["step_ids"] == ["read-source"]
    assert requirement_id in coverage["unresolved_requirement_ids"]
    assert coverage["requirements"][0]["verification_status"] == "unverified"


def test_planner_catalogue_exposes_omissions_and_keeps_long_text_bounded():
    orchestrator = _orchestrator(requirements=tuple(f"criterion {index}: " + "x" * 2000 for index in range(64)))

    prompt = outcome_planning_context(orchestrator)
    catalog = _catalog(prompt)

    assert catalog["omitted_requirements"] > 0
    assert catalog["omitted_requirements"] + len(catalog["requirements"]) == catalog["total_requirements"]
    assert all(len(item["text"]) <= 240 and item["text_truncated"] for item in catalog["requirements"])
    assert len(prompt) < 10_000


def test_catalogue_budget_includes_admitted_obligations():
    orchestrator = _orchestrator()
    orchestrator.agent_state = SimpleNamespace(
        task_definition_ref=orchestrator.agent_state.task_definition_ref,
        plan=Plan(),
        task_semantics=SimpleNamespace(obligations=tuple(
            SimpleNamespace(id=f"obligation-{index}", kind="deliver", description="x" * 160)
            for index in range(100)
        )),
    )

    catalog = _catalog(outcome_planning_context(orchestrator))

    assert len(json.dumps(catalog, ensure_ascii=False, separators=(",", ":"))) <= MAX_CATALOG_CHARS
    assert catalog["total_obligations"] == 100
    assert catalog["omitted_obligations"] == 100 - len(catalog["admitted_obligations"])


def test_requirement_links_cannot_resolve_through_another_workspace():
    orchestrator = _orchestrator()
    requirement_id = _catalog(outcome_planning_context(orchestrator))["requirements"][0]["id"]
    plan = Plan.from_raw([{"tool": "file_reader", "args": {}, "requirement_ids": [requirement_id]}])
    orchestrator.workspace_paths.workspace_id = "foreign-workspace"

    with pytest.raises(PlanCoverageError, match="workspace"):
        validate_outcome_plan(orchestrator, plan)


def test_public_coverage_redacts_credentials_and_freezes_without_mutating_source():
    state = AgentState()
    source = {"requirements": [{"requirement_id": "req-stable", "text": "password=PRIVATE_VALUE"}]}

    facts = build_run_projection_facts(state, observed_at="2026-10-26T00:00:00Z", plan_coverage=source)

    assert "PRIVATE_VALUE" not in repr(facts.plan_coverage)
    assert "[REDACTED]" in facts.plan_coverage["requirements"][0]["text"]
    assert source["requirements"][0]["text"] == "password=PRIVATE_VALUE"
    source["requirements"].clear()
    assert facts.plan_coverage["requirements"][0]["requirement_id"] == "req-stable"


def test_legacy_state_without_definition_has_no_invented_coverage():
    assert plan_coverage_document(AgentState()) == {}

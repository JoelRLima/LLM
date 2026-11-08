from __future__ import annotations

import json
from dataclasses import replace
from types import SimpleNamespace

from llm_agent.agent.planning.outcome_planning import outcome_planning_context, plan_coverage_document
from llm_agent.agent.planning.outcome_verification import requirement_verification_document
from llm_agent.agent.reporting.run_projection_facts import build_run_projection_facts
from llm_agent.agent.state import AgentState
from llm_agent.agent.task_definition.models import TaskDefinitionRef, VerificationCheck
from llm_agent.agent.task_definition.resolver import TaskContextResolver
from tests.support.task_definition import make_contract, make_spec


def _orchestrator(root, checks):
    contract = replace(
        make_contract("verified", "Read notes.txt", requirements=("Deliver JSON",)),
        verification_checks=checks,
    )
    spec = make_spec(contract)
    context = TaskContextResolver().materialize(contract, spec, workspace_id="workspace")
    state = AgentState()
    state.task_definition_ref = TaskDefinitionRef(
        task_id=contract.task_id, contract_version=contract.version,
        contract_digest=contract.digest(), spec_version=spec.version,
        spec_digest=spec.digest(), definition_state="complete",
    )
    return SimpleNamespace(
        agent_state=state, workspace_root=root,
        task_context_resolver=SimpleNamespace(resolve=lambda _ref: context),
        workspace_paths=SimpleNamespace(workspace_id="workspace"),
    )


def _check(kind="json_fields"):
    return VerificationCheck(
        "delivery", kind, "requirements", 0, "result.json",
        ("answer",) if kind == "json_fields" else (),
    )


def test_fresh_observation_changes_after_file_edit_and_frozen_publication_does_not(tmp_path):
    orchestrator = _orchestrator(tmp_path, (_check(),))
    target = tmp_path / "result.json"
    target.write_text('{"answer":42}', encoding="utf-8")
    first = plan_coverage_document(orchestrator)
    facts = build_run_projection_facts(
        orchestrator.agent_state, observed_at="2026-10-26T00:00:00Z", plan_coverage=first,
    )
    checked = next(row for row in first["requirements"] if row["source"] == "contract.requirements")
    assert checked["status"] == "unmapped"
    assert checked["verification_status"] == "satisfied"
    assert checked["verification_scope"] == "declared_checks_only"
    assert checked["requirement_id"] not in first["unresolved_requirement_ids"]
    json.dumps(first)
    target.write_text('{}', encoding="utf-8")
    current = requirement_verification_document(orchestrator)
    assert current["overall_status"] == "violated"
    assert current["checks"][0]["evidence_digest"] != first["requirement_verification"]["checks"][0]["evidence_digest"]
    assert facts.plan_coverage["requirement_verification"]["overall_status"] == "satisfied"


def test_multiple_checks_for_one_statement_all_must_pass(tmp_path):
    checks = (_check("file_exists"), replace(_check(), check_id="shape"))
    orchestrator = _orchestrator(tmp_path, checks)
    (tmp_path / "result.json").write_text('{}', encoding="utf-8")
    document = plan_coverage_document(orchestrator)
    row = next(item for item in document["requirements"] if item["source"] == "contract.requirements")
    assert row["verification_status"] == "violated"
    assert row["requirement_id"] in document["unresolved_requirement_ids"]
    prompt = json.loads(outcome_planning_context(orchestrator).splitlines()[2])
    assert prompt["requirement_verification"]["overall_status"] == "violated"


def test_missing_workspace_or_wrong_definition_is_inconclusive(tmp_path):
    orchestrator = _orchestrator(tmp_path, (_check(),))
    del orchestrator.workspace_root
    assert requirement_verification_document(orchestrator)["overall_status"] == "inconclusive"
    orchestrator.workspace_paths.workspace_id = "foreign"
    assert requirement_verification_document(orchestrator)["status"] == "unavailable"


def test_legacy_definition_has_no_check_without_accessing_workspace(tmp_path):
    orchestrator = _orchestrator(tmp_path, ())
    del orchestrator.workspace_root
    assert requirement_verification_document(orchestrator) == {}

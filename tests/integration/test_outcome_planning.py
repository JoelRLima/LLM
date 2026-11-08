from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from llm_agent.agent.application import AgentApplication
from llm_agent.agent.approval import AutoApprove
from llm_agent.agent.llm.contracts import ModelRequest, ModelResponse
from llm_agent.agent.llm.decision_contract import ModelRequestContract
from llm_agent.agent.runtime.config_repository import ConfigRepository
from llm_agent.agent.task_definition.models import TaskContract, TaskSpec
from llm_agent.agent.task_definition.repository import TaskDefinitionRepository
from llm_agent.agent.task_definition.requirement_refs import requirement_references
from llm_agent.agent.task_definition.resolver import TaskContextResolver
from llm_agent.workspace.paths import AppPaths
from tests.integration.test_agent_application_evaluation import JourneyGateway


def _initialized_paths(root: Path) -> AppPaths:
    paths = AppPaths.discover(root / "home", env={})
    ConfigRepository(paths).initialize()
    return paths


class OutcomePlanningGateway(JourneyGateway):
    """Existing deterministic model edge with one controlled plan-link variant."""

    def __init__(
        self,
        objective: str,
        association: str = "valid",
        scenario_id: str | None = None,
    ) -> None:
        super().__init__(objective, scenario_id)
        self.association = association
        self._last_task_spec: TaskSpec | None = None
        self.emitted_requirement_id: str | None = None

    def complete(self, request: ModelRequest) -> ModelResponse:
        response = super().complete(request)
        request_contract = getattr(request.request_contract, "value", request.request_contract)
        if request_contract == ModelRequestContract.TASK_SPEC.value:
            raw = json.loads(response.content)
            self._last_task_spec = TaskSpec.from_dict(raw["spec"])
            return response
        if request_contract != ModelRequestContract.INITIAL_PLAN.value:
            return response

        payload = json.loads(response.content)
        plan = payload.get("plan")
        if self.association == "legacy" or not isinstance(plan, list) or not plan:
            return response
        contract = self._last_task_contract
        spec = self._last_task_spec
        if not isinstance(contract, TaskContract) or not isinstance(spec, TaskSpec):
            raise AssertionError("plan request arrived before admitted task authority")
        if self.association == "stale":
            stale_contract = replace(
                contract,
                requirements=("A requirement from a prior definition digest.",),
            )
            stale_spec = replace(spec, contract_digest=stale_contract.digest())
            refs = requirement_references(
                TaskContextResolver().materialize(stale_contract, stale_spec).structured
            )
            requirement_id = refs[0].requirement_id
        elif self.association == "unknown":
            requirement_id = "req-not-in-active-catalogue"
        else:
            refs = requirement_references(
                TaskContextResolver().materialize(contract, spec).structured
            )
            requirement_id = refs[0].requirement_id
        self.emitted_requirement_id = requirement_id
        plan[0]["requirement_ids"] = [requirement_id]
        return ModelResponse(
            content=json.dumps(payload, ensure_ascii=False),
            reasoning=response.reasoning,
            usage=response.usage,
            finish_reason=response.finish_reason,
            provider_metadata=dict(response.provider_metadata),
        )


def _run(
    tmp_path: Path,
    objective: str,
    *,
    association: str = "valid",
    scenario_id: str | None = None,
) -> tuple[Any, Any, Path]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    if scenario_id == "CAP_RECOVERY":
        (workspace / "sample.py").write_text("value = 1\n", encoding="utf-8")
    else:
        (workspace / "notes.txt").write_text("CAP_READ_EVIDENCE\n", encoding="utf-8")
    gateway = OutcomePlanningGateway(objective, association, scenario_id)
    with AgentApplication.create(
        paths=_initialized_paths(tmp_path),
        workspace=workspace,
        gateway=gateway,
        approval_policy=AutoApprove(),
        configure_logging=False,
    ) as application:
        result = application.run(objective)
        state = application.orchestrator.agent_state
        history = tuple(state.tool_history)
        snapshot = result.snapshot
        assert snapshot is not None
        reference = state.task_definition_ref
        assert reference is not None
        repository = TaskDefinitionRepository(application.workspace_paths)
        materialization = TaskContextResolver(repository).resolve(reference)
        canonical_requirement_ids = tuple(
            item.requirement_id for item in materialization.requirement_references
        )
        return result, (gateway, state, snapshot, history, canonical_requirement_ids), workspace


def test_active_requirement_link_runs_and_projects_invocation_without_verifying_criterion(
    tmp_path: Path,
) -> None:
    objective = "CAP_READ: leia notes.txt e informe CAP_READ_EVIDENCE."
    result, (gateway, _state, snapshot, history, catalog_ids), _workspace = _run(
        tmp_path, objective
    )

    assert result.success is True
    assert gateway.emitted_requirement_id is not None
    assert [item["tool"] for item in history] == ["file_reader"]
    expected_requirement_id = catalog_ids[0]
    assert snapshot.projection_facts.canonical_plan[0]["requirement_ids"] == (
        expected_requirement_id,
    )
    projection = snapshot.projection_facts.plan_coverage
    requirement = next(
        item for item in projection["requirements"]
        if item["requirement_id"] == expected_requirement_id
    )
    assert projection["status"] == "available"
    assert projection["associations_are_claims"] is True
    assert requirement["status"] == "linked"
    assert requirement["verification_status"] == "unverified"
    step = requirement["steps"][0]
    assert step["step_status"] == "completed"
    assert len(step["invocation_refs"]) == 1


@pytest.mark.parametrize("association", ["unknown", "stale"])
def test_unknown_or_stale_requirement_link_is_rejected_before_tool_invocation(
    tmp_path: Path,
    association: str,
) -> None:
    objective = "CAP_READ: leia notes.txt e informe CAP_READ_EVIDENCE."
    result, (gateway, state, snapshot, history, catalog_ids), workspace = _run(
        tmp_path, objective, association=association
    )

    assert result.success is False
    assert result.status == "blocked"
    assert gateway.emitted_requirement_id is not None
    if association == "stale":
        assert gateway.emitted_requirement_id not in catalog_ids
    assert history == ()
    assert state.tool_history == []
    assert (workspace / "notes.txt").read_text(encoding="utf-8") == "CAP_READ_EVIDENCE\n"
    assert snapshot.projection_facts.invocation_evidence == ()


def test_legacy_plan_executes_with_explicit_unmapped_coverage(tmp_path: Path) -> None:
    objective = "CAP_READ: leia notes.txt e informe CAP_READ_EVIDENCE."
    result, (gateway, _state, snapshot, history, _catalog_ids), _workspace = _run(
        tmp_path, objective, association="legacy"
    )

    assert result.success is True
    assert gateway.emitted_requirement_id is None
    assert [item["tool"] for item in history] == ["file_reader"]
    coverage = snapshot.projection_facts.plan_coverage
    assert coverage["requirements"]
    assert all(item["status"] == "unmapped" for item in coverage["requirements"])
    assert all(
        item["verification_status"] == "unverified"
        for item in coverage["requirements"]
    )


def test_failed_unvalidated_mutation_stays_failed_when_plan_has_requirement_link(
    tmp_path: Path,
) -> None:
    result, (gateway, state, snapshot, history, catalog_ids), workspace = _run(
        tmp_path,
        "modifique sample.py",
        scenario_id="CAP_RECOVERY",
    )

    assert result.success is False
    assert result.status == "failed"
    assert gateway.emitted_requirement_id is not None
    assert len(history) == 1
    assert (workspace / "sample.py").read_text(encoding="utf-8") == "value = 1\n"
    assert result.receipt["validation"] == {"ran": True, "outcome": "failed"}
    assert result.receipt["rollback"]["occurred"] is True
    coverage = snapshot.projection_facts.plan_coverage
    expected_requirement_id = catalog_ids[0]
    requirement = next(
        item for item in coverage["requirements"]
        if item["requirement_id"] == expected_requirement_id
    )
    assert requirement["status"] == "linked"
    assert requirement["verification_status"] == "unverified"
    assert requirement["steps"][0]["step_status"] == "failed"


def test_checkpoint_restore_and_validated_plan_extension_keep_each_link_once(
    tmp_path: Path,
) -> None:
    objective = "CAP_READ: leia notes.txt e informe CAP_READ_EVIDENCE."
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "notes.txt").write_text("CAP_READ_EVIDENCE\n", encoding="utf-8")
    (workspace / "notes-copy.txt").write_text("CAP_READ_EVIDENCE\n", encoding="utf-8")
    gateway = OutcomePlanningGateway(objective)
    with AgentApplication.create(
        paths=_initialized_paths(tmp_path),
        workspace=workspace,
        gateway=gateway,
        approval_policy=AutoApprove(),
        configure_logging=False,
    ) as application:
        result = application.run(objective)
        assert result.success is True
        state = application.orchestrator.agent_state
        reference = state.task_definition_ref
        assert reference is not None
        repository = TaskDefinitionRepository(application.workspace_paths)
        materialization = TaskContextResolver(repository).resolve(reference)
        requirement_id = materialization.requirement_references[0].requirement_id

        checkpoint = state.to_checkpoint_dict()
        state.from_checkpoint_dict(checkpoint)
        assert state.plan[0].requirement_ids == (requirement_id,)

        extension = application.orchestrator.execution_gateway.extend_validated_plan(
            [
                {
                    "tool": "file_reader",
                    "args": {"file_path": "notes-copy.txt"},
                    "requirement_ids": [requirement_id],
                }
            ],
            objective,
        )
        assert extension is not None
        associations = [step.requirement_ids for step in state.plan]
        assert associations == [(requirement_id,), (requirement_id,)]
        assert all(len(set(link_ids or ())) == len(link_ids or ()) for link_ids in associations)

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from agent.application import AgentApplication
from agent.approval import AutoApprove
from agent.evaluation.scripted_gateway import ScriptedEvaluationGateway
from agent.llm.admitted_decisions import (
    TaskContractDecision,
    TaskSpecDecision,
    admit_typed_model_decision,
)
from agent.llm.contracts import ModelRequest, ModelResponse, ProviderCapabilities
from agent.llm.decision_contract import ModelRequestContract
from agent.orchestration.task_runner import TaskInputs, TaskRunner
from agent.runtime.budget import TaskBudgetLedger
from agent.runtime.config_repository import ConfigRepository
from agent.runtime.paths import AppPaths
from agent.state import AgentState
from agent.task_definition.compiler import TaskDefinitionCompiler
from agent.task_definition.errors import (
    TaskDefinitionCompilationError,
    TaskDefinitionMismatchError,
    TaskDefinitionValidationError,
)
from agent.task_definition.models import TaskDefinitionRef
from agent.task_definition.repository import TaskDefinitionRepository
from agent.task_definition.resolver import TaskContextResolver
from agent.task_definition.serialization import (
    MAX_CONTRACT_BYTES,
    MAX_SPEC_BYTES,
    canonical_json_bytes,
    serialize_contract,
    serialize_spec,
)
from tests.support.task_definition import make_contract, make_phase, make_spec


class _GatewayWithoutTaskDefinitionSupport:
    provider_name = "unsupported-fixture"
    model = "unsupported-fixture"
    capabilities = ProviderCapabilities(streaming=False)

    def __init__(self) -> None:
        self.calls: list[ModelRequest] = []

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.calls.append(request)
        if request.request_contract is not ModelRequestContract.TASK_CONTRACT:
            raise AssertionError("planner/tool path was reached without task authority")
        return ModelResponse(
            content='{"action":"direct_response","answer":"bypass attempt"}'
        )

    def stream(self, request: ModelRequest):
        del request
        raise AssertionError("unsupported fixture must not stream")

    def count_tokens(self, text: str) -> int:
        return max(1, len(text) // 4)


def _initialized_paths(tmp_path: Any) -> AppPaths:
    paths = AppPaths.discover(tmp_path / "home", env={})
    ConfigRepository(paths).initialize()
    return paths


def test_gateway_without_task_definition_support_cannot_reach_planner_or_tools(
    tmp_path: Any,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    gateway = _GatewayWithoutTaskDefinitionSupport()

    with AgentApplication.create(
        paths=_initialized_paths(tmp_path),
        workspace=workspace,
        gateway=gateway,
        approval_policy=AutoApprove(),
        configure_logging=False,
    ) as application:
        result = application.run("Leia notes.txt e informe o conteudo observado.")

        assert result.status == "blocked"
        assert result.success is False
        assert gateway.calls
        assert {
            request.request_contract for request in gateway.calls
        } == {ModelRequestContract.TASK_CONTRACT}
        assert application.orchestrator.agent_state.tool_history == []
        assert not any(
            event.get("type") == "plan_created"
            for event in application.orchestrator.agent_state.events
        )


def test_scripted_evaluation_persists_authority_before_normal_execution(tmp_path: Any) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "h1_observation.txt").write_text(
        "H1_OBSERVED_EVIDENCE\n",
        encoding="utf-8",
    )
    objective = "H1_WORKSPACE: leia h1_observation.txt e informe H1_OBSERVED_EVIDENCE."
    gateway = ScriptedEvaluationGateway(objective)

    with AgentApplication.create(
        paths=_initialized_paths(tmp_path),
        workspace=workspace,
        gateway=gateway,
        approval_policy=AutoApprove(),
        configure_logging=False,
    ) as application:
        result = application.run(objective)
        state = application.orchestrator.agent_state
        assert result.success is True
        assert "file_reader" in [entry["tool"] for entry in state.tool_history]

        contracts = [request.request_contract for request in gateway.calls]
        assert contracts[:2] == [
            ModelRequestContract.TASK_CONTRACT,
            ModelRequestContract.TASK_SPEC,
        ]
        assert ModelRequestContract.INITIAL_PLAN in contracts

        reference = state.task_definition_ref
        assert reference is not None
        record = TaskDefinitionRepository(application.workspace_paths).load(
            reference.task_id
        )
        assert record.contract is not None
        assert record.spec is not None
        assert record.reference.is_complete


def _contract_at_limit() -> Any:
    return make_contract(
        "boundary-contract",
        requirements=tuple(["x" * 8192] * 31 + ["x" * 7689]),
    )


def _contract_over_limit() -> Any:
    return make_contract(
        "boundary-contract",
        requirements=tuple(["x" * 8192] * 31 + ["x" * 7690]),
    )


def _spec_at_limit(contract: Any) -> Any:
    return make_spec(
        contract,
        global_requirements=tuple(["x" * 8192] * 60),
        global_invariants=tuple(["x" * 8192] * 60),
        global_acceptance=tuple(["x" * 8192] * 7 + ["x" * 7219]),
    )


def _spec_over_limit(contract: Any) -> Any:
    return make_spec(
        contract,
        global_requirements=tuple(["x" * 8192] * 60),
        global_invariants=tuple(["x" * 8192] * 60),
        global_acceptance=tuple(["x" * 8192] * 7 + ["x" * 7220]),
    )


def _selected_reference(reference: TaskDefinitionRef, phase_id: str) -> TaskDefinitionRef:
    return TaskDefinitionRef(
        task_id=reference.task_id,
        contract_version=reference.contract_version,
        contract_digest=reference.contract_digest,
        spec_version=reference.spec_version,
        spec_digest=reference.spec_digest,
        definition_state=reference.definition_state,
        active_phase_id=phase_id,
    )


def test_canonical_authority_limits_cover_exact_boundary_and_one_byte_over() -> None:
    contract = _contract_at_limit()
    contract_over = _contract_over_limit()
    assert len(canonical_json_bytes(contract.to_dict())) == MAX_CONTRACT_BYTES
    assert len(serialize_contract(contract)) == MAX_CONTRACT_BYTES
    with pytest.raises(TaskDefinitionValidationError):
        serialize_contract(contract_over)

    spec = _spec_at_limit(make_contract("boundary-spec"))
    spec_over = _spec_over_limit(make_contract("boundary-spec"))
    assert len(canonical_json_bytes(spec.to_dict())) == MAX_SPEC_BYTES
    assert len(serialize_spec(spec)) == MAX_SPEC_BYTES
    with pytest.raises(TaskDefinitionValidationError):
        serialize_spec(spec_over)


def test_model_admission_rejects_oversized_contract_and_spec_before_projection() -> None:
    contract_value = {
        "action": "define_contract",
        "contract": _contract_over_limit().to_dict(),
    }
    spec_contract = make_contract("oversized-spec-admission")
    spec_value = {
        "action": "define_spec",
        "spec": _spec_over_limit(spec_contract).to_dict(),
    }

    assert (
        admit_typed_model_decision(
            contract_value,
            request_contract=ModelRequestContract.TASK_CONTRACT,
            step_type="task_contract",
        )
        is None
    )
    assert (
        admit_typed_model_decision(
            spec_value,
            request_contract=ModelRequestContract.TASK_SPEC,
            step_type="task_spec",
        )
        is None
    )

    exact_contract = admit_typed_model_decision(
        {"action": "define_contract", "contract": _contract_at_limit().to_dict()},
        request_contract=ModelRequestContract.TASK_CONTRACT,
        step_type="task_contract",
    )
    exact_spec = admit_typed_model_decision(
        {
            "action": "define_spec",
            "spec": _spec_at_limit(make_contract("boundary-spec")).to_dict(),
        },
        request_contract=ModelRequestContract.TASK_SPEC,
        step_type="task_spec",
    )
    assert isinstance(exact_contract, TaskContractDecision)
    assert isinstance(exact_spec, TaskSpecDecision)


def test_oversized_contract_is_rejected_before_any_repository_path_is_created(repository) -> None:
    contract = _contract_over_limit()
    compiler = TaskDefinitionCompiler(
        repository,
        contract_provider=lambda *_args: {
            "action": "define_contract",
            "contract": contract.to_dict(),
        },
    )

    with pytest.raises(TaskDefinitionCompilationError) as caught:
        compiler.compile(contract.task_id, contract.objective)

    assert caught.value.reason_code == "CONTRACT_ADMISSION_FAILED"
    assert not repository.task_dir(contract.task_id).exists()
    assert not repository.manifest_path(contract.task_id).exists()
    assert not repository.contract_path(contract.task_id).exists()


def test_oversized_spec_is_rejected_before_spec_body_and_preserves_contract(repository) -> None:
    contract = make_contract("oversized-spec-persistence", "oversized spec")
    repository.save_contract(contract)
    original_contract = repository.contract_path(contract.task_id).read_bytes()
    oversized = _spec_over_limit(contract)
    compiler = TaskDefinitionCompiler(
        repository,
        spec_provider=lambda _contract: {
            "action": "define_spec",
            "spec": oversized.to_dict(),
        },
    )

    with pytest.raises(TaskDefinitionCompilationError) as caught:
        compiler.compile(contract.task_id, contract.objective)

    assert caught.value.reason_code == "SPEC_ADMISSION_FAILED"
    assert repository.contract_path(contract.task_id).read_bytes() == original_contract
    assert repository.load(contract.task_id).spec is None
    assert not repository.spec_path(contract.task_id).exists()
    manifest = json.loads(repository.manifest_path(contract.task_id).read_text())
    assert manifest["state"] == "contract_ready"


def test_repository_persists_the_same_validated_bytes_used_for_digest(repository) -> None:
    contract = make_contract("validated-bytes")
    reference = repository.save_contract(contract)
    assert repository.contract_path(contract.task_id).read_bytes() == serialize_contract(contract)
    assert reference.contract_digest == contract.digest()

    spec = make_spec(contract)
    complete = repository.save_spec(spec)
    assert repository.spec_path(contract.task_id).read_bytes() == serialize_spec(spec)
    assert complete.spec_digest == spec.digest()


def test_resume_round_trip_preserves_active_phase_through_checkpoint_runner_and_context(
    repository,
) -> None:
    contract = make_contract("resume-active-phase", "resume active phase")
    repository.save_contract(contract)
    repository.save_spec(
        make_spec(
            contract,
            phases=(
                make_phase("phase-1"),
                make_phase("phase-2", depends_on=("phase-1",)),
            ),
        )
    )
    persisted = repository.load_ref(contract.task_id)
    selected = _selected_reference(persisted, "phase-2")

    state = AgentState(budget_ledger=TaskBudgetLedger())
    state.objective = contract.objective
    state.root_task_id = contract.task_id
    state.task_definition_ref = selected
    checkpoint = state.to_checkpoint_dict()

    restored = AgentState(budget_ledger=TaskBudgetLedger())
    restored.from_checkpoint_dict(checkpoint)
    assert restored.task_definition_ref == selected

    resolver = TaskContextResolver(repository)
    compiler = TaskDefinitionCompiler(
        repository,
        contract_provider=lambda *_args: pytest.fail("resume regenerated Contract"),
        spec_provider=lambda *_args: pytest.fail("resume regenerated Spec"),
    )
    saves: list[bool] = []
    orchestrator = SimpleNamespace(
        agent_state=restored,
        task_definition_compiler=compiler,
        task_context_resolver=resolver,
        _save_checkpoint=lambda: saves.append(True) or True,
        _preserve_checkpoint=False,
        _emit=lambda *_args, **_kwargs: None,
    )

    answer = TaskRunner(orchestrator)._ensure_task_definition(
        TaskInputs(contract.objective, True, 0)
    )

    assert answer is None
    assert saves
    assert restored.task_definition_ref == selected
    materialized = resolver.resolve(restored.task_definition_ref)
    assert materialized.phase_id == "phase-2"
    assert '"phase_selection":"phase-2"' in materialized.trusted_text


def test_resume_rejects_unknown_active_phase_without_regenerating_authority(repository) -> None:
    contract = make_contract("resume-unknown-phase", "resume unknown phase")
    repository.save_contract(contract)
    repository.save_spec(make_spec(contract))
    persisted = repository.load_ref(contract.task_id)
    unknown = _selected_reference(persisted, "unknown-phase")
    compiler = TaskDefinitionCompiler(
        repository,
        contract_provider=lambda *_args: pytest.fail("unknown phase regenerated Contract"),
        spec_provider=lambda *_args: pytest.fail("unknown phase regenerated Spec"),
    )

    with pytest.raises(TaskDefinitionMismatchError):
        compiler.resume(contract.task_id, unknown)
    with pytest.raises(TaskDefinitionMismatchError):
        TaskContextResolver(repository).resolve(unknown)

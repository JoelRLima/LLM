from __future__ import annotations

from typing import Any

import pytest

from agent.llm.decision_contract import ModelRequestContract
from agent.task_definition.compiler import TaskDefinitionCompiler
from agent.task_definition.errors import (
    TaskDefinitionCompilationError,
    TaskDefinitionMismatchError,
    TaskDefinitionNeedsInput,
)
from agent.task_definition.models import TaskDefinitionRef
from tests.support.task_definition import make_contract, make_spec


def _contract_decision(task_id: str, objective: str) -> dict[str, Any]:
    return {
        "action": "define_contract",
        "contract": make_contract(task_id, objective).to_dict(),
    }


def _spec_decision(contract: Any) -> dict[str, Any]:
    return {"action": "define_spec", "spec": make_spec(contract).to_dict()}


def test_fresh_compile_persists_contract_before_spec_and_returns_complete_ref(repository) -> None:
    calls: list[str] = []
    contract = make_contract("compile-task", "compile objective")

    def contract_provider(task_id: str, objective: str) -> dict[str, Any]:
        calls.append("contract")
        return {"action": "define_contract", "contract": contract.to_dict()}

    def spec_provider(persisted: Any) -> dict[str, Any]:
        calls.append("spec")
        assert repository.load(persisted.task_id).spec is None
        assert not repository.spec_path(persisted.task_id).exists()
        return _spec_decision(persisted)

    ref = TaskDefinitionCompiler(
        repository,
        contract_provider=contract_provider,
        spec_provider=spec_provider,
    ).compile(contract.task_id, contract.objective)

    assert calls == ["contract", "spec"]
    assert isinstance(ref, TaskDefinitionRef)
    assert ref.is_complete
    assert repository.load(ref.task_id).reference == ref


def test_contract_needs_input_stops_before_spec(repository) -> None:
    spec_calls: list[bool] = []

    def spec_provider(_contract: Any) -> Any:
        spec_calls.append(True)
        return None

    compiler = TaskDefinitionCompiler(
        repository,
        contract_provider=lambda *_args: {
            "action": "needs_input",
            "reason": "ambiguous target",
            "question": "Which target?",
        },
        spec_provider=spec_provider,
    )
    with pytest.raises(TaskDefinitionNeedsInput):
        compiler.compile("needs-input", "clarify")
    assert spec_calls == []
    assert repository.inspect("needs-input") is None


def test_contract_provider_failure_is_stable_and_does_not_create_definition(repository) -> None:
    def broken(*_args: Any) -> Any:
        raise RuntimeError("provider down")

    compiler = TaskDefinitionCompiler(repository, contract_provider=broken)
    with pytest.raises(TaskDefinitionCompilationError) as caught:
        compiler.compile("provider-failure", "objective")
    assert caught.value.reason_code == "TASK_DEFINITION_PROVIDER_FAILED"
    assert repository.inspect("provider-failure") is None


def test_spec_provider_failure_leaves_recoverable_contract_ready(repository) -> None:
    contract = make_contract("partial-task", "partial objective")

    def broken(_contract: Any) -> Any:
        raise RuntimeError("temporary spec outage")

    compiler = TaskDefinitionCompiler(
        repository,
        contract_provider=lambda *_args: _contract_decision(
            contract.task_id, contract.objective
        ),
        spec_provider=broken,
    )
    with pytest.raises(TaskDefinitionCompilationError) as caught:
        compiler.compile(contract.task_id, contract.objective)

    assert caught.value.reason_code == "TASK_DEFINITION_PROVIDER_FAILED"
    partial = compiler.last_ref
    assert partial is not None
    assert partial.definition_state == "contract_ready"
    assert repository.load(contract.task_id).spec is None


def test_resume_reuses_exact_persisted_contract_and_calls_only_spec_provider(repository) -> None:
    contract = make_contract("resume-task", "resume objective")
    first = TaskDefinitionCompiler(
        repository,
        contract_provider=lambda *_args: _contract_decision(
            contract.task_id, contract.objective
        ),
        spec_provider=lambda _contract: (_ for _ in ()).throw(RuntimeError("retry")),
    )
    with pytest.raises(TaskDefinitionCompilationError):
        first.compile(contract.task_id, contract.objective)
    partial = first.last_ref
    assert partial is not None
    calls: list[str] = []

    def must_not_regenerate(*_args: Any) -> Any:
        calls.append("contract")
        raise AssertionError("resume regenerated Contract")

    def expand(persisted: Any) -> dict[str, Any]:
        calls.append("spec")
        assert persisted == repository.load_contract(contract.task_id)
        return _spec_decision(persisted)

    complete = TaskDefinitionCompiler(
        repository,
        contract_provider=must_not_regenerate,
        spec_provider=expand,
    ).resume(contract.task_id, partial)

    assert complete.is_complete
    assert calls == ["spec"]


def test_wrong_contract_binding_rejects_spec_without_complete_manifest(repository) -> None:
    contract = make_contract("binding-task", "binding objective")

    def wrong_spec(persisted: Any) -> dict[str, Any]:
        spec = make_spec(persisted, contract_digest="0" * 64)
        return {"action": "define_spec", "spec": spec.to_dict()}

    compiler = TaskDefinitionCompiler(
        repository,
        contract_provider=lambda *_args: _contract_decision(
            contract.task_id, contract.objective
        ),
        spec_provider=wrong_spec,
    )
    with pytest.raises(TaskDefinitionMismatchError):
        compiler.compile(contract.task_id, contract.objective)
    assert repository.load(contract.task_id).reference.definition_state == "contract_ready"


def test_model_compilation_uses_exact_authority_request_contracts_without_recursive_context(
    repository,
) -> None:
    calls: list[tuple[ModelRequestContract, str, bool]] = []
    contract = make_contract("model-task", "model objective")

    class FakeContextManager:
        def ask_model_typed(
            self,
            _prompt: str,
            *,
            request_contract: ModelRequestContract,
            step_type: str,
            include_task_definition: bool,
        ) -> Any:
            calls.append((request_contract, step_type, include_task_definition))
            if request_contract is ModelRequestContract.TASK_CONTRACT:
                return _contract_decision(contract.task_id, contract.objective)
            return _spec_decision(contract)

    ref = TaskDefinitionCompiler(repository, context_manager=FakeContextManager()).compile(
        contract.task_id, contract.objective
    )

    assert ref.is_complete
    assert calls == [
        (ModelRequestContract.TASK_CONTRACT, "task_contract", False),
        (ModelRequestContract.TASK_SPEC, "task_spec", False),
    ]


def test_completed_definition_collision_with_different_objective_fails_closed(repository) -> None:
    contract = make_contract("collision-task", "first objective")
    compiler = TaskDefinitionCompiler(
        repository,
        contract_provider=lambda *_args: _contract_decision(
            contract.task_id, contract.objective
        ),
        spec_provider=lambda persisted: _spec_decision(persisted),
    )
    compiler.compile(contract.task_id, contract.objective)

    with pytest.raises(TaskDefinitionMismatchError):
        compiler.compile(contract.task_id, "different objective")

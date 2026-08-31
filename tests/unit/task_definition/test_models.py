from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from agent.task_definition.errors import (
    TaskDefinitionMismatchError,
    TaskDefinitionValidationError,
)
from agent.task_definition.models import (
    MAX_COLLECTION_ITEMS,
    MAX_PHASES,
    MAX_STRING_LENGTH,
    TaskContract,
    TaskDefinitionRef,
    TaskSpec,
    TaskSpecPhase,
)
from agent.task_definition.serialization import (
    contract_digest,
    deserialize_contract,
    deserialize_ref,
    deserialize_spec,
    serialize_contract,
    serialize_ref,
    serialize_spec,
    spec_digest,
)
from tests.support.task_definition import make_contract, make_phase, make_spec


def test_valid_contract_is_normalized_and_immutable() -> None:
    contract = TaskContract(
        task_id="safe_task",
        objective="objective",
        requirements=["one", "two"],
    )

    assert contract.requirements == ("one", "two")
    assert isinstance(contract.to_dict()["requirements"], list)
    with pytest.raises(FrozenInstanceError):
        contract.objective = "changed"
    projected = contract.to_dict()
    projected["requirements"].append("mutated")
    assert contract.requirements == ("one", "two")


@pytest.mark.parametrize("task_id", ["", "../escape", "a/b", "a b", ".hidden"])
def test_invalid_task_id_is_rejected(task_id: str) -> None:
    with pytest.raises(TaskDefinitionValidationError):
        TaskContract(task_id=task_id, objective="objective")


@pytest.mark.parametrize("version", [0, -1, True, "1"])
def test_invalid_versions_are_rejected(version: object) -> None:
    with pytest.raises(TaskDefinitionValidationError):
        TaskContract(task_id="task-1", objective="objective", version=version)  # type: ignore[arg-type]


def test_unknown_and_executable_contract_fields_are_rejected() -> None:
    raw = make_contract().to_dict()
    raw["tool"] = "shell"

    with pytest.raises(TaskDefinitionValidationError):
        TaskContract.from_dict(raw)


def test_contract_collection_and_string_bounds_are_enforced() -> None:
    with pytest.raises(TaskDefinitionValidationError):
        make_contract(requirements=tuple("x" for _ in range(MAX_COLLECTION_ITEMS + 1)))
    with pytest.raises(TaskDefinitionValidationError):
        make_contract(objective="x" * (MAX_STRING_LENGTH + 1))


def test_contract_digest_is_deterministic_and_round_trips() -> None:
    contract = make_contract(
        requirements=("first", "second"),
        constraints=("bounded",),
    )

    assert contract_digest(contract) == "13d6c1989d3eca315c033e940c0ca921882cc3b9f8ec422c66667bce1a642ed7"
    assert deserialize_contract(serialize_contract(contract)) == contract
    assert deserialize_contract({"objective": contract.objective, "task_id": contract.task_id}).version == 1


def test_valid_spec_is_bound_to_contract_and_round_trips() -> None:
    contract = make_contract()
    spec = make_spec(contract)

    assert spec_digest(spec) == spec.digest()
    assert deserialize_spec(serialize_spec(spec)) == spec
    assert spec.phases[0].depends_on == ()


def test_spec_rejects_binding_mismatch_and_unknown_executable_fields() -> None:
    contract = make_contract()
    wrong_task = make_spec(contract, task_id="other-task")
    with pytest.raises(TaskDefinitionMismatchError):
        wrong_task.validate_against(contract)

    raw = make_spec(contract).to_dict()
    raw["phases"][0]["tool"] = "shell"
    with pytest.raises(TaskDefinitionValidationError):
        TaskSpec.from_dict(raw)


def test_spec_rejects_version_and_digest_mismatch() -> None:
    contract = make_contract()
    with pytest.raises(TaskDefinitionMismatchError):
        make_spec(contract, contract_version=2).validate_against(contract)
    with pytest.raises(TaskDefinitionMismatchError):
        make_spec(contract, contract_digest="0" * 64).validate_against(contract)


def test_spec_rejects_duplicate_missing_and_cyclic_phases() -> None:
    contract = make_contract()
    duplicate = (make_phase("phase-1"), make_phase("phase-1"))
    with pytest.raises(TaskDefinitionValidationError):
        make_spec(contract, phases=duplicate)

    with pytest.raises(TaskDefinitionValidationError):
        make_spec(contract, phases=(make_phase("phase-1", depends_on=("missing",)),))

    cycle = (
        make_phase("phase-1", depends_on=("phase-2",)),
        make_phase("phase-2", depends_on=("phase-1",)),
    )
    with pytest.raises(TaskDefinitionValidationError):
        make_spec(contract, phases=cycle)


def test_spec_phase_and_count_bounds_are_enforced() -> None:
    contract = make_contract()
    with pytest.raises(TaskDefinitionValidationError):
        make_spec(
            contract,
            phases=tuple(make_phase(f"phase-{index}") for index in range(MAX_PHASES + 1)),
        )
    with pytest.raises(TaskDefinitionValidationError):
        TaskSpecPhase(
            phase_id="phase-1",
            title="title",
            goal="goal",
            requirements=tuple("x" for _ in range(MAX_COLLECTION_ITEMS + 1)),
        )


def test_reference_is_compact_strict_and_versionable() -> None:
    contract = make_contract()
    spec = make_spec(contract)
    reference = TaskDefinitionRef(
        task_id=contract.task_id,
        contract_version=contract.version,
        contract_digest=contract.digest(),
        spec_version=spec.version,
        spec_digest=spec.digest(),
        definition_state="complete",
        active_phase_id="phase-1",
    )

    assert deserialize_ref(serialize_ref(reference)) == reference
    assert "objective" not in serialize_ref(reference).decode("utf-8")
    with pytest.raises(TaskDefinitionValidationError):
        TaskDefinitionRef.from_dict({**reference.to_dict(), "contract": {}})
    with pytest.raises(TaskDefinitionValidationError):
        TaskDefinitionRef(
            task_id=contract.task_id,
            contract_version=contract.version,
            contract_digest=contract.digest(),
            definition_state="contract_ready",
            active_phase_id="phase-1",
        )

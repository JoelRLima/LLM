from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from agent.task_definition.errors import (
    TaskDefinitionMismatchError,
    TaskDefinitionMissingError,
    TaskDefinitionPersistenceError,
    TaskDefinitionValidationError,
)
from agent.task_definition.models import TaskSpecPhase
from agent.task_definition.repository import (
    CONTRACT_FILE_NAME,
    MANIFEST_FILE_NAME,
    SPEC_FILE_NAME,
    TaskDefinitionRepository,
)
from tests.support.task_definition import make_contract, make_spec


def test_contract_ready_and_complete_manifest_round_trip(repository: TaskDefinitionRepository) -> None:
    contract = make_contract()
    ready = repository.save_contract(contract)

    assert ready.definition_state == "contract_ready"
    assert json.loads(repository.manifest_path(contract.task_id).read_text(encoding="utf-8"))["state"] == "contract_ready"
    assert repository.load(contract.task_id).spec is None
    assert repository.contract_path(contract.task_id).is_file()

    spec = make_spec(contract)
    complete = repository.save_spec(spec)
    assert complete.is_complete
    record = repository.load(contract.task_id)
    assert record.spec == spec
    assert record.reference == complete
    assert repository.spec_path(contract.task_id).name == SPEC_FILE_NAME.format(version=1)


def test_immutable_contract_and_spec_versions_cannot_be_conflictingly_overwritten(
    repository: TaskDefinitionRepository,
) -> None:
    contract = make_contract()
    repository.save_contract(contract)
    assert repository.save_contract(contract) == repository.load_ref(contract.task_id)
    with pytest.raises(TaskDefinitionMismatchError):
        repository.save_contract(make_contract(summary="different"))

    spec = make_spec(contract)
    repository.save_spec(spec)
    with pytest.raises(TaskDefinitionMismatchError):
        repository.save_spec(make_spec(contract, architecture="different"))


@pytest.mark.parametrize("task_id", ["", "../escape", "a/b", "a\\b", "a b", "."])
def test_repository_rejects_traversal_or_unsafe_task_ids(
    repository: TaskDefinitionRepository,
    task_id: str,
) -> None:
    with pytest.raises(TaskDefinitionValidationError):
        repository.task_dir(task_id)


def test_repository_resolves_an_explicit_existing_active_phase(
    repository: TaskDefinitionRepository,
) -> None:
    contract = make_contract()
    repository.save_contract(contract)
    repository.save_spec(
        make_spec(
            contract,
            phases=(
                TaskSpecPhase(
                    phase_id="phase-1",
                    title="First",
                    goal="first",
                ),
                TaskSpecPhase(
                    phase_id="phase-2",
                    title="Second",
                    goal="second",
                    depends_on=("phase-1",),
                ),
            ),
        )
    )
    persisted = repository.load_ref(contract.task_id)
    selected = type(persisted)(
        task_id=persisted.task_id,
        contract_version=persisted.contract_version,
        contract_digest=persisted.contract_digest,
        spec_version=persisted.spec_version,
        spec_digest=persisted.spec_digest,
        definition_state=persisted.definition_state,
        active_phase_id="phase-2",
    )

    assert repository.resolve(selected).spec is not None
    with pytest.raises(TaskDefinitionMismatchError):
        repository.resolve(
            type(selected)(
                task_id=selected.task_id,
                contract_version=selected.contract_version,
                contract_digest=selected.contract_digest,
                spec_version=selected.spec_version,
                spec_digest=selected.spec_digest,
                definition_state=selected.definition_state,
                active_phase_id="missing",
            )
        )


def test_corrupt_manifest_contract_spec_and_digest_fail_closed(
    repository: TaskDefinitionRepository,
) -> None:
    manifest_contract = make_contract("manifest-task")
    repository.save_contract(manifest_contract)
    manifest_path = repository.manifest_path(manifest_contract.task_id)

    manifest_path.write_text("{", encoding="utf-8")
    with pytest.raises(TaskDefinitionValidationError):
        repository.inspect(manifest_contract.task_id)

    contract = make_contract("contract-task")
    repository.save_contract(contract)
    repository.contract_path(contract.task_id).write_text("{}", encoding="utf-8")
    with pytest.raises(TaskDefinitionValidationError):
        repository.inspect(contract.task_id)

    spec_contract = make_contract("spec-task")
    repository.save_contract(spec_contract)
    repository.save_spec(make_spec(spec_contract))
    repository.spec_path(spec_contract.task_id).write_text("{}", encoding="utf-8")
    with pytest.raises(TaskDefinitionValidationError):
        repository.inspect(spec_contract.task_id)

    digest_contract_value = make_contract("digest-task")
    repository.save_contract(digest_contract_value)
    repository.save_spec(make_spec(digest_contract_value))
    digest_manifest_path = repository.manifest_path(digest_contract_value.task_id)
    manifest = json.loads(digest_manifest_path.read_text(encoding="utf-8"))
    manifest["contract"]["digest"] = "0" * 64
    digest_manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(TaskDefinitionMismatchError):
        repository.inspect(digest_contract_value.task_id)


def test_missing_referenced_file_and_workspace_mismatch_are_distinct(
    repository: TaskDefinitionRepository,
) -> None:
    contract = make_contract()
    repository.save_contract(contract)
    repository.contract_path(contract.task_id).unlink()
    with pytest.raises(TaskDefinitionMissingError):
        repository.load(contract.task_id)

    other_contract = make_contract("other-task")
    repository.save_contract(other_contract)
    other = TaskDefinitionRepository(repository.root_dir, workspace_id="other-workspace")
    with pytest.raises(TaskDefinitionMismatchError):
        other.inspect(other_contract.task_id)


def test_repository_rejects_link_like_task_directory(
    repository: TaskDefinitionRepository,
    tmp_path: Path,
) -> None:
    target = tmp_path / "outside"
    target.mkdir()
    link = repository.root_dir / "linked"
    try:
        os.symlink(target, link, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")
    with pytest.raises(TaskDefinitionValidationError):
        repository.task_dir("linked")


def test_repository_uses_expected_versioned_file_names(
    repository: TaskDefinitionRepository,
) -> None:
    contract = make_contract()
    repository.save_contract(contract)
    assert repository.manifest_path(contract.task_id).name == MANIFEST_FILE_NAME
    assert repository.contract_path(contract.task_id).name == CONTRACT_FILE_NAME.format(version=1)
    with pytest.raises(TaskDefinitionPersistenceError):
        repository._create_immutable(
            repository.contract_path(contract.task_id),
            b"{}",
            "Contract",
        )

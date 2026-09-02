"""Strict manifest and filesystem helpers for the task-definition repository."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any, Mapping, cast

from agent.memory.path_safety import LinkLikePathError, reject_link_like
from agent.runtime.filesystem_primitives import sync_parent_directory
from agent.runtime.path_safety import WorkspacePathError, resolve_workspace_path
from agent.task_definition.errors import (
    TaskDefinitionError,
    TaskDefinitionMismatchError,
    TaskDefinitionMissingError,
    TaskDefinitionPersistenceError,
    TaskDefinitionValidationError,
)
from agent.task_definition.model_validation import (
    DEFINITION_STATES,
    DIGEST_PATTERN,
)
from agent.task_definition.models import TaskDefinitionRecord, TaskDefinitionRef
from agent.task_definition.serialization import (
    contract_digest,
    deserialize_contract,
    deserialize_spec,
    spec_digest,
)


def inspect_repository(repository: Any, task_id: str) -> TaskDefinitionRecord | None:
    """Load and validate one repository record through its canonical seams."""

    task_dir = repository.task_dir(task_id)
    if not _valid_task_directory(task_dir):
        return None
    manifest = _read_manifest(repository, task_id)
    validated = repository._validate_manifest(manifest, task_id)
    contract_ref = validated["contract"]
    contract = _read_contract(repository, task_id, task_dir, contract_ref)
    _verify_contract(contract, contract_ref)
    if validated["state"] == "contract_ready":
        reference = TaskDefinitionRef(
            task_id=task_id,
            contract_version=contract.version,
            contract_digest=contract_ref["digest"],
            definition_state="contract_ready",
        )
        return TaskDefinitionRecord(contract, None, reference, repository.workspace_id)
    spec_ref = validated.get("spec")
    if not isinstance(spec_ref, dict):
        raise TaskDefinitionValidationError("manifest complete sem referência de Spec")
    spec = _read_spec(repository, task_id, task_dir, spec_ref)
    if spec.version != spec_ref["version"] or spec_digest(spec) != spec_ref["digest"]:
        raise TaskDefinitionMismatchError("Spec persistida não corresponde ao manifest.")
    spec.validate_against(contract)
    reference = TaskDefinitionRef(
        task_id=task_id,
        contract_version=contract.version,
        contract_digest=contract_ref["digest"],
        spec_version=spec.version,
        spec_digest=spec_ref["digest"],
        definition_state="complete",
    )
    return TaskDefinitionRecord(contract, spec, reference, repository.workspace_id)


def _valid_task_directory(task_dir: Path) -> bool:
    try:
        inspection = reject_link_like(task_dir)
    except LinkLikePathError as exc:
        raise TaskDefinitionValidationError(str(exc)) from exc
    if not inspection.exists:
        return False
    if inspection.metadata is None or not stat.S_ISDIR(inspection.metadata.st_mode):
        raise TaskDefinitionValidationError(f"diretório de tarefa inválido: {task_dir}")
    return True


def _read_manifest(repository: Any, task_id: str) -> dict[str, Any]:
    try:
        return cast(
            dict[str, Any],
            repository._read_object(repository.manifest_path(task_id), "manifest"),
        )
    except FileNotFoundError as exc:
        raise TaskDefinitionValidationError(
            f"manifest ausente para a tarefa '{task_id}'"
        ) from exc


def _read_contract(
    repository: Any,
    task_id: str,
    task_dir: Path,
    contract_ref: Mapping[str, Any],
) -> Any:
    contract_file = repository._referenced_file(task_dir, contract_ref["file"], "Contract")
    try:
        return deserialize_contract(repository._read_bytes(contract_file, "Contract"))
    except FileNotFoundError as exc:
        raise TaskDefinitionMissingError(task_id, path=contract_file) from exc
    except TaskDefinitionError:
        raise
    except Exception as exc:
        raise TaskDefinitionValidationError(f"Contract inválido para '{task_id}'") from exc


def _verify_contract(contract: Any, contract_ref: Mapping[str, Any]) -> None:
    if contract.version != contract_ref["version"] or contract_digest(contract) != contract_ref["digest"]:
        raise TaskDefinitionMismatchError("Contract persistido não corresponde ao manifest.")


def _read_spec(
    repository: Any,
    task_id: str,
    task_dir: Path,
    spec_ref: Mapping[str, Any],
) -> Any:
    spec_file = repository._referenced_file(task_dir, spec_ref["file"], "Spec")
    try:
        return deserialize_spec(repository._read_bytes(spec_file, "Spec"))
    except FileNotFoundError as exc:
        raise TaskDefinitionMissingError(task_id, path=spec_file) from exc
    except TaskDefinitionError:
        raise
    except Exception as exc:
        raise TaskDefinitionValidationError(f"Spec inválida para '{task_id}'") from exc


def create_immutable(path: Path, payload: bytes, label: str) -> None:
    """Create one versioned body with exclusive creation and fsync."""

    try:
        reject_link_like(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0),
            0o600,
        )
        try:
            with os.fdopen(descriptor, "wb") as stream:
                descriptor = -1
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
        finally:
            if descriptor != -1:
                os.close(descriptor)
        sync_parent_directory(path)
    except FileExistsError as exc:
        raise TaskDefinitionPersistenceError(
            f"{label} versionado já existe e não pode ser sobrescrito: {path}"
        ) from exc
    except (LinkLikePathError, OSError) as exc:
        raise TaskDefinitionPersistenceError(
            f"{label} não pôde ser criado como arquivo imutável: {path}"
        ) from exc


def validate_manifest(
    manifest: Mapping[str, Any],
    task_id: str,
    *,
    workspace_id: str,
    schema_version: int,
    contract_file_name: str,
    spec_file_name: str,
) -> dict[str, Any]:
    if not isinstance(manifest, Mapping):
        raise TaskDefinitionValidationError("manifest deve ser um objeto")
    allowed = {"schema_version", "task_id", "workspace_id", "state", "contract", "spec"}
    if sorted(set(manifest) - allowed):
        raise TaskDefinitionValidationError("manifest possui campos desconhecidos")
    if manifest.get("schema_version") != schema_version:
        raise TaskDefinitionValidationError("versão de manifest incompatível")
    if manifest.get("task_id") != task_id:
        raise TaskDefinitionMismatchError("task_id do manifest não corresponde ao diretório")
    if manifest.get("workspace_id") != workspace_id:
        raise TaskDefinitionMismatchError("workspace_id de outra workspace")
    state = manifest.get("state")
    if state not in DEFINITION_STATES:
        raise TaskDefinitionValidationError("estado de manifest inválido")
    contract = validate_artifact_ref(
        manifest.get("contract"),
        "Contract",
        task_id,
        required=True,
        contract_file_name=contract_file_name,
        spec_file_name=spec_file_name,
    )
    spec = validate_artifact_ref(
        manifest.get("spec"),
        "Spec",
        task_id,
        required=state == "complete",
        contract_file_name=contract_file_name,
        spec_file_name=spec_file_name,
    )
    if state == "contract_ready" and spec is not None:
        raise TaskDefinitionValidationError("contract_ready não pode referenciar Spec")
    return {"state": state, "contract": contract, "spec": spec}


def validate_artifact_ref(
    value: Any,
    label: str,
    task_id: str,
    *,
    required: bool,
    contract_file_name: str,
    spec_file_name: str,
) -> dict[str, Any] | None:
    if value is None and not required:
        return None
    if not isinstance(value, Mapping):
        raise TaskDefinitionValidationError(f"referência de {label} ausente ou inválida")
    if set(value) != {"version", "digest", "file"}:
        raise TaskDefinitionValidationError(f"referência de {label} possui campos inválidos")
    version = value["version"]
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise TaskDefinitionValidationError(f"versão de {label} inválida")
    artifact_digest = value["digest"]
    if not isinstance(artifact_digest, str) or not DIGEST_PATTERN.fullmatch(artifact_digest):
        raise TaskDefinitionValidationError(f"digest de {label} inválido")
    filename = value["file"]
    template = contract_file_name if label == "Contract" else spec_file_name
    expected = template.format(version=version)
    if filename != expected or not isinstance(filename, str) or Path(filename).name != filename:
        raise TaskDefinitionValidationError(f"arquivo de {label} inválido para '{task_id}'")
    return {"version": version, "digest": artifact_digest, "file": filename}


def referenced_file(task_dir: Path, filename: str, label: str) -> Path:
    try:
        return resolve_workspace_path(task_dir, filename)
    except (OSError, RuntimeError, WorkspacePathError) as exc:
        raise TaskDefinitionValidationError(f"arquivo de {label} fora da tarefa") from exc


def read_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(read_bytes(path, label).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TaskDefinitionValidationError(f"{label} não pôde ser lido: {path}") from exc
    if not isinstance(value, dict):
        raise TaskDefinitionValidationError(f"{label} deve ter raiz objeto")
    return value


def read_bytes(path: Path, label: str) -> bytes:
    try:
        reject_link_like(path)
        with path.open("rb") as stream:
            return stream.read()
    except FileNotFoundError:
        raise
    except (LinkLikePathError, OSError) as exc:
        raise TaskDefinitionValidationError(f"{label} não pôde ser lido: {path}") from exc


def reject_link_ancestors(candidate: Path, root_dir: Path) -> None:
    try:
        relative = candidate.relative_to(root_dir)
    except ValueError as exc:
        raise TaskDefinitionValidationError("caminho de task definition fora do root") from exc
    current = root_dir
    for component in relative.parts:
        current = current / component
        try:
            inspection = reject_link_like(current)
        except LinkLikePathError as exc:
            raise TaskDefinitionValidationError(str(exc)) from exc
        if inspection.exists and inspection.metadata is not None and component != relative.parts[-1]:
            if not stat.S_ISDIR(inspection.metadata.st_mode):
                raise TaskDefinitionValidationError(f"ancestral de task definition inválido: {current}")


def same_definition_identity(
    persisted: TaskDefinitionRef,
    supplied: TaskDefinitionRef,
) -> bool:
    return (
        persisted.task_id == supplied.task_id
        and persisted.contract_version == supplied.contract_version
        and persisted.contract_digest == supplied.contract_digest
        and persisted.definition_state == supplied.definition_state
        and persisted.spec_version == supplied.spec_version
        and persisted.spec_digest == supplied.spec_digest
    )

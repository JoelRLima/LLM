"""Durable, workspace-scoped storage for immutable task definitions."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from agent.memory.json_persistence import write_json_atomic
from agent.memory.path_safety import reject_link_like
from agent.runtime.paths import WorkspacePaths
from agent.task_definition.errors import (
    TaskDefinitionError,
    TaskDefinitionMismatchError,
    TaskDefinitionMissingError,
    TaskDefinitionPersistenceError,
    TaskDefinitionValidationError,
)
from agent.task_definition.models import (
    TASK_ID_PATTERN,
    TaskContract,
    TaskDefinitionRecord,
    TaskDefinitionRef,
    TaskSpec,
)
from agent.task_definition.repository_support import (
    create_immutable,
    inspect_repository,
    read_bytes,
    read_object,
    referenced_file,
    reject_link_ancestors,
    same_definition_identity,
    validate_manifest,
)
from agent.task_definition.serialization import (
    contract_digest,
    serialize_contract,
    serialize_spec,
    spec_digest,
)

MANIFEST_SCHEMA_VERSION = 1
MANIFEST_FILE_NAME = "manifest.json"
CONTRACT_FILE_NAME = "contract.v{version}.json"
SPEC_FILE_NAME = "spec.v{version}.json"


class TaskDefinitionRepository:
    """Own the only canonical Contract/Spec persistence domain."""

    def __init__(
        self,
        workspace_paths: WorkspacePaths | str | Path,
        *,
        workspace_id: str | None = None,
    ) -> None:
        if isinstance(workspace_paths, WorkspacePaths):
            root = workspace_paths.task_definitions_dir
            selected_workspace_id = workspace_paths.workspace_id
        else:
            root = Path(workspace_paths)
            selected_workspace_id = workspace_id or "workspace"
        self.workspace_id = self._validate_workspace_id(selected_workspace_id)
        self.root_dir = Path(root).expanduser().resolve()

    @staticmethod
    def _validate_workspace_id(value: Any) -> str:
        if not isinstance(value, str) or not value.strip():
            raise TaskDefinitionValidationError("workspace_id: valor vazio")
        return value

    @staticmethod
    def validate_task_id(task_id: Any) -> str:
        if not isinstance(task_id, str) or not TASK_ID_PATTERN.fullmatch(task_id):
            raise TaskDefinitionValidationError(
                "task_id: formato invalido ou potencialmente traversavel"
            )
        return task_id

    def task_dir(self, task_id: str) -> Path:
        selected = self.validate_task_id(task_id)
        candidate = self.root_dir / selected
        try:
            candidate.relative_to(self.root_dir)
        except ValueError as exc:
            raise TaskDefinitionValidationError("task_id: caminho fora do repositorio") from exc
        self._reject_link_ancestors(candidate)
        return candidate

    definition_dir = task_dir

    def manifest_path(self, task_id: str) -> Path:
        return self.task_dir(task_id) / MANIFEST_FILE_NAME

    def contract_path(self, task_id: str, version: int = 1) -> Path:
        self.validate_task_id(task_id)
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise TaskDefinitionValidationError("contract_version: versao invalida")
        return self.task_dir(task_id) / CONTRACT_FILE_NAME.format(version=version)

    def spec_path(self, task_id: str, version: int = 1) -> Path:
        self.validate_task_id(task_id)
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise TaskDefinitionValidationError("spec_version: versao invalida")
        return self.task_dir(task_id) / SPEC_FILE_NAME.format(version=version)

    def exists(self, task_id: str) -> bool:
        return self.task_dir(task_id).exists()

    def inspect(self, task_id: str) -> TaskDefinitionRecord | None:
        return inspect_repository(self, task_id)

    def load(self, task_id: str) -> TaskDefinitionRecord:
        record = self.inspect(task_id)
        if record is None:
            raise TaskDefinitionMissingError(task_id, path=self.task_dir(task_id))
        return record

    load_definition = load

    def load_ref(self, task_id: str) -> TaskDefinitionRef:
        return self.load(task_id).reference

    def load_contract(self, task_id: str) -> TaskContract:
        return self.load(task_id).contract

    def load_spec(self, task_id: str) -> TaskSpec:
        record = self.load(task_id)
        if record.spec is None:
            raise TaskDefinitionMismatchError("Spec ainda nao foi persistida (contract_ready).")
        return record.spec

    def save_contract(self, contract: TaskContract) -> TaskDefinitionRef:
        if not isinstance(contract, TaskContract):
            raise TaskDefinitionValidationError("contract deve ser uma TaskContract")
        contract_payload = serialize_contract(contract)
        task_id = self.validate_task_id(contract.task_id)
        task_dir = self.task_dir(task_id)
        existing = self.inspect(task_id)
        if existing is not None:
            if existing.contract != contract:
                raise TaskDefinitionMismatchError(
                    f"Contract v{contract.version} ja existe com conteudo diferente."
                )
            return existing.reference
        try:
            task_dir.mkdir(parents=True, exist_ok=False)
            self._reject_link_ancestors(task_dir)
            contract_path = self.contract_path(task_id, contract.version)
            self._create_immutable(contract_path, contract_payload, "Contract")
            self._publish_manifest(self._contract_ready_manifest(contract))
        except TaskDefinitionError:
            raise
        except (OSError, ValueError, TypeError) as exc:
            raise TaskDefinitionPersistenceError(
                f"falha ao persistir Contract de '{task_id}': {type(exc).__name__}"
            ) from exc
        return TaskDefinitionRef(
            task_id=task_id,
            contract_version=contract.version,
            contract_digest=contract_digest(contract),
            definition_state="contract_ready",
        )

    persist_contract = save_contract
    create_contract = save_contract

    def save_spec(self, spec: TaskSpec) -> TaskDefinitionRef:
        if not isinstance(spec, TaskSpec):
            raise TaskDefinitionValidationError("spec deve ser uma TaskSpec")
        task_id = self.validate_task_id(spec.task_id)
        record = self.load(task_id)
        spec.validate_against(record.contract)
        spec_payload = serialize_spec(spec)
        if record.spec is not None:
            if record.spec != spec:
                raise TaskDefinitionMismatchError(
                    f"Spec v{spec.version} ja existe com conteudo diferente."
                )
            return record.reference
        try:
            spec_path = self.spec_path(task_id, spec.version)
            self._create_immutable(spec_path, spec_payload, "Spec")
            self._publish_manifest(self._complete_manifest(record.contract, spec))
        except TaskDefinitionError:
            raise
        except (OSError, ValueError, TypeError) as exc:
            raise TaskDefinitionPersistenceError(
                f"falha ao persistir Spec de '{task_id}': {type(exc).__name__}"
            ) from exc
        return TaskDefinitionRef(
            task_id=task_id,
            contract_version=record.contract.version,
            contract_digest=contract_digest(record.contract),
            spec_version=spec.version,
            spec_digest=spec_digest(spec),
            definition_state="complete",
        )

    persist_spec = save_spec
    create_spec = save_spec

    def resolve(self, reference: TaskDefinitionRef) -> TaskDefinitionRecord:
        if not isinstance(reference, TaskDefinitionRef):
            raise TaskDefinitionValidationError("reference deve ser uma TaskDefinitionRef")
        record = self.load(reference.task_id)
        if not same_definition_identity(record.reference, reference):
            raise TaskDefinitionMismatchError("referencia nao corresponde a definicao persistida")
        if reference.active_phase_id is not None:
            if record.spec is None or reference.active_phase_id not in {
                phase.phase_id for phase in record.spec.phases
            }:
                raise TaskDefinitionMismatchError(
                    f"phase_id nao encontrado: {reference.active_phase_id}"
                )
        return record

    def _contract_ready_manifest(self, contract: TaskContract) -> dict[str, Any]:
        return {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "task_id": contract.task_id,
            "workspace_id": self.workspace_id,
            "state": "contract_ready",
            "contract": {
                "version": contract.version,
                "digest": contract_digest(contract),
                "file": CONTRACT_FILE_NAME.format(version=contract.version),
            },
            "spec": None,
        }

    def _complete_manifest(self, contract: TaskContract, spec: TaskSpec) -> dict[str, Any]:
        return {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "task_id": contract.task_id,
            "workspace_id": self.workspace_id,
            "state": "complete",
            "contract": {
                "version": contract.version,
                "digest": contract_digest(contract),
                "file": CONTRACT_FILE_NAME.format(version=contract.version),
            },
            "spec": {
                "version": spec.version,
                "digest": spec_digest(spec),
                "file": SPEC_FILE_NAME.format(version=spec.version),
            },
        }

    def _publish_manifest(self, manifest: Mapping[str, Any]) -> None:
        task_id = self.validate_task_id(manifest.get("task_id"))
        path = self.manifest_path(task_id)
        try:
            reject_link_like(path)
            write_json_atomic(path, dict(manifest))
        except Exception as exc:
            if isinstance(exc, TaskDefinitionError):
                raise
            raise TaskDefinitionPersistenceError(
                f"falha ao publicar manifest de '{task_id}': {type(exc).__name__}"
            ) from exc

    _create_immutable = staticmethod(create_immutable)

    def _validate_manifest(
        self,
        manifest: Mapping[str, Any],
        task_id: str,
    ) -> dict[str, Any]:
        return validate_manifest(
            manifest,
            task_id,
            workspace_id=self.workspace_id,
            schema_version=MANIFEST_SCHEMA_VERSION,
            contract_file_name=CONTRACT_FILE_NAME,
            spec_file_name=SPEC_FILE_NAME,
        )

    _referenced_file = staticmethod(referenced_file)
    _read_object = staticmethod(read_object)
    _read_bytes = staticmethod(read_bytes)

    def _reject_link_ancestors(self, candidate: Path) -> None:
        reject_link_ancestors(candidate, self.root_dir)


__all__ = [
    "CONTRACT_FILE_NAME",
    "MANIFEST_FILE_NAME",
    "MANIFEST_SCHEMA_VERSION",
    "SPEC_FILE_NAME",
    "TaskDefinitionRepository",
]

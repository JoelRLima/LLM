"""Application use case for reading persisted task-definition authority."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import TypeAlias

from llm_agent.agent.task_definition.errors import TaskDefinitionError
from llm_agent.agent.task_definition.repository import TaskDefinitionRepository
from llm_agent.agent.task_definition.resolver import TaskContextResolver
from llm_agent.application.context import AppPaths, WorkspaceContext

JsonValue: TypeAlias = (
    str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
)
FrozenJsonValue: TypeAlias = (
    str
    | int
    | float
    | bool
    | None
    | tuple["FrozenJsonValue", ...]
    | Mapping[str, "FrozenJsonValue"]
)


@dataclass(frozen=True, slots=True)
class TaskContextRequest:
    """Inputs for materializing one task's persisted authority."""

    app_paths: AppPaths
    workspace: str | Path
    task_id: str
    phase_id: str | None = None


@dataclass(frozen=True, slots=True)
class TaskContextResult:
    """Application-owned projection of materialized task authority."""

    task_id: str
    workspace_id: str
    contract_version: int
    contract_digest: str
    spec_version: int
    spec_digest: str
    phase_id: str | None
    trusted_text: str
    authority: Mapping[str, FrozenJsonValue]

    def to_dict(self) -> dict[str, JsonValue]:
        """Return the stable CLI document using ordinary JSON containers."""

        return {
            "task_id": self.task_id,
            "workspace_id": self.workspace_id,
            "contract_version": self.contract_version,
            "contract_digest": self.contract_digest,
            "spec_version": self.spec_version,
            "spec_digest": self.spec_digest,
            "phase_id": self.phase_id,
            "context": self.trusted_text,
            "authority": _copy_json_object(self.authority),
        }


class TaskContextReadError(RuntimeError):
    """Application error for an invalid or unavailable task definition."""


def _copy_json(value: object) -> JsonValue:
    if isinstance(value, Mapping):
        copied: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("task authority projection contains a non-string key")
            copied[key] = _copy_json(item)
        return copied
    if isinstance(value, (tuple, list)):
        return [_copy_json(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError("task authority projection contains a non-JSON value")


def _copy_json_object(value: object) -> dict[str, JsonValue]:
    copied = _copy_json(value)
    if not isinstance(copied, dict):
        raise TypeError("task authority projection must be an object")
    return copied


def _freeze_json(value: object) -> FrozenJsonValue:
    if isinstance(value, Mapping):
        frozen: dict[str, FrozenJsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("task authority projection contains a non-string key")
            frozen[key] = _freeze_json(item)
        return MappingProxyType(frozen)
    if isinstance(value, (tuple, list)):
        return tuple(_freeze_json(item) for item in value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError("task authority projection contains a non-JSON value")


def _freeze_json_object(value: object) -> Mapping[str, FrozenJsonValue]:
    frozen = _freeze_json(value)
    if not isinstance(frozen, Mapping):
        raise TypeError("task authority projection must be an object")
    return frozen


def read_task_context(request: TaskContextRequest) -> TaskContextResult:
    """Read and materialize persisted authority without mutating workspace state."""

    try:
        workspace_context = WorkspaceContext.create(request.workspace)
        workspace_paths = request.app_paths.for_workspace(workspace_context.workspace_id)
        repository = TaskDefinitionRepository(workspace_paths)
        resolver = TaskContextResolver(repository)
        materialization = resolver.resolve(
            request.task_id,
            phase_id=request.phase_id,
        )
    except TaskDefinitionError as exc:
        raise TaskContextReadError(str(exc)) from exc

    return TaskContextResult(
        task_id=materialization.task_id,
        workspace_id=materialization.workspace_id,
        contract_version=materialization.contract_version,
        contract_digest=materialization.contract_digest,
        spec_version=materialization.spec_version,
        spec_digest=materialization.spec_digest,
        phase_id=materialization.phase_id,
        trusted_text=materialization.trusted_text,
        authority=_freeze_json_object(materialization.structured),
    )


__all__ = [
    "TaskContextReadError",
    "TaskContextRequest",
    "TaskContextResult",
    "read_task_context",
]

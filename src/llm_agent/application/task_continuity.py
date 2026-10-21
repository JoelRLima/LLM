"""Application query for one workspace's task continuity."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import TypeAlias

from llm_agent.agent.continuity.service import TaskContinuityService
from llm_agent.application.context import AppPaths, WorkspaceContext

JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
FrozenJsonValue: TypeAlias = (
    str | int | float | bool | None | tuple["FrozenJsonValue", ...] | Mapping[str, "FrozenJsonValue"]
)


@dataclass(frozen=True, slots=True)
class TaskContinuityRequest:
    """Select the canonical continuity slot for one workspace."""

    app_paths: AppPaths
    workspace: str | Path


@dataclass(frozen=True, slots=True)
class TaskContinuityResult:
    """Application-owned continuity facts and an isolated JSON document."""

    status: str
    reason_code: str
    resumable: bool
    _document: Mapping[str, FrozenJsonValue]

    def to_dict(self) -> dict[str, JsonValue]:
        """Return fresh ordinary JSON containers for the CLI."""

        return _copy_json_object(self._document)


def _freeze_json(value: object) -> FrozenJsonValue:
    if isinstance(value, Mapping):
        frozen: dict[str, FrozenJsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("continuity document contains a non-string key")
            frozen[key] = _freeze_json(item)
        return MappingProxyType(frozen)
    if isinstance(value, (tuple, list)):
        return tuple(_freeze_json(item) for item in value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError("continuity document contains a non-JSON value")


def _freeze_json_object(value: object) -> Mapping[str, FrozenJsonValue]:
    frozen = _freeze_json(value)
    if not isinstance(frozen, Mapping):
        raise TypeError("continuity document must be an object")
    return frozen


def _copy_json(value: object) -> JsonValue:
    if isinstance(value, Mapping):
        copied: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("continuity document contains a non-string key")
            copied[key] = _copy_json(item)
        return copied
    if isinstance(value, (tuple, list)):
        return [_copy_json(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError("continuity document contains a non-JSON value")


def _copy_json_object(value: object) -> dict[str, JsonValue]:
    copied = _copy_json(value)
    if not isinstance(copied, dict):
        raise TypeError("continuity document must be an object")
    return copied


def read_task_continuity(request: TaskContinuityRequest) -> TaskContinuityResult:
    """Read the canonical checkpoint once and project its continuity facts."""

    workspace_context = WorkspaceContext.create(request.workspace)
    workspace_paths = request.app_paths.for_workspace(workspace_context.workspace_id)
    service = TaskContinuityService(workspace_paths)
    snapshot = service.snapshot()
    return TaskContinuityResult(
        status=snapshot.status.value,
        reason_code=snapshot.reason_code,
        resumable=snapshot.resumable,
        _document=_freeze_json_object(snapshot.to_dict()),
    )


__all__ = ["TaskContinuityRequest", "TaskContinuityResult", "read_task_continuity"]

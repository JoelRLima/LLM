"""Canonical request boundary and runtime lineage validation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from agent.tools.json_snapshot import freeze_json_like, thaw_json_like


def _validate_lineage(request: "ToolInvocationRequest") -> None:
    names = ("task_id", "run_id", "root_task_id", "parent_task_id", "node_id")
    for name in names:
        value = getattr(request, name)
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ValueError(f"{name} deve ser uma string não vazia")
    lineage = (request.run_id, request.root_task_id, request.parent_task_id, request.node_id)
    if any(value is not None for value in lineage) and not all(
        value is not None for value in (request.run_id, request.root_task_id, request.task_id)
    ):
        raise ValueError("runtime lineage requer run_id, root_task_id e task_id")
    if request.run_id is None:
        return
    is_child = request.task_id != request.root_task_id
    if is_child and (request.parent_task_id is None or request.node_id is None):
        raise ValueError("child runtime lineage requer parent_task_id e node_id")
    if not is_child and (request.parent_task_id is not None or request.node_id is not None):
        raise ValueError("root runtime lineage nao aceita parent_task_id ou node_id")


@dataclass(frozen=True, slots=True)
class ToolInvocationRequest:
    """Validated invocation boundary prepared before gateway integration."""

    invocation_id: str
    tool_name: str
    arguments: Mapping[str, Any] = field(default_factory=dict)
    timeout_seconds: int | None = None
    task_id: str | None = field(default=None, kw_only=True)
    run_id: str | None = field(default=None, kw_only=True)
    root_task_id: str | None = field(default=None, kw_only=True)
    parent_task_id: str | None = field(default=None, kw_only=True)
    node_id: str | None = field(default=None, kw_only=True)

    def __post_init__(self) -> None:
        if type(self.invocation_id) is not str or not self.invocation_id.strip():
            raise ValueError("invocation_id deve ser uma string não vazia")
        if type(self.tool_name) is not str or not self.tool_name.strip():
            raise ValueError("tool_name deve ser uma string não vazia")
        if not isinstance(self.arguments, Mapping):
            raise TypeError("arguments deve ser um mapping")
        object.__setattr__(self, "arguments", freeze_json_like(dict(self.arguments)))
        if self.timeout_seconds is not None and (
            type(self.timeout_seconds) is not int or self.timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds deve ser um inteiro positivo")
        _validate_lineage(self)

    def __getattribute__(self, name: str) -> Any:
        if name == "arguments":
            return thaw_json_like(object.__getattribute__(self, "arguments"))
        return object.__getattribute__(self, name)


__all__ = ["ToolInvocationRequest"]

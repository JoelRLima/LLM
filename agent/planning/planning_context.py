"""Pure, safe projection of runtime tools into a planning snapshot."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping
from uuid import uuid4

from agent.tools.authority import (
    ApplicationAuthoritySnapshot,
    TaskAuthoritySnapshot,
    derive_effective_task_authority,
)
from agent.tools.contracts import (
    ToolDescriptor,
    ToolOriginKind,
    freeze_json_like,
    thaw_json_like,
)
from agent.tools.extension_state import validate_extension_id
from agent.tools.runtime_identity import RuntimeSnapshotIdentity


class PlanningContextError(ValueError):
    """Raised when a descriptor or authority snapshot is structurally incoherent."""


@dataclass(frozen=True, slots=True)
class PlanningTool:
    name: str
    description: str
    input_schema: Mapping[str, Any] = field(default_factory=dict)
    required_capabilities: frozenset[str] = field(default_factory=frozenset)
    origin_kind: ToolOriginKind = ToolOriginKind.BUILTIN
    extension_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise PlanningContextError("PlanningTool requer nome")
        if not isinstance(self.description, str):
            raise PlanningContextError("PlanningTool requer descrição textual")
        object.__setattr__(self, "input_schema", freeze_json_like(dict(self.input_schema)))
        object.__setattr__(self, "required_capabilities", frozenset(self.required_capabilities))
        if not isinstance(self.origin_kind, ToolOriginKind):
            object.__setattr__(self, "origin_kind", ToolOriginKind(str(self.origin_kind)))
        if self.origin_kind is ToolOriginKind.EXTENSION:
            if not isinstance(self.extension_id, str) or not self.extension_id.strip():
                raise PlanningContextError("Tool de extension requer extension_id")
            try:
                validate_extension_id(self.extension_id)
            except ValueError as exc:
                raise PlanningContextError("extension_id inválido") from exc
        elif self.extension_id is not None:
            raise PlanningContextError("Tool builtin não pode conter extension_id")

    def __getattribute__(self, name: str) -> Any:
        if name == "input_schema":
            return thaw_json_like(object.__getattribute__(self, "input_schema"))
        return object.__getattribute__(self, name)


@dataclass(frozen=True, slots=True)
class PlanningContextSnapshot:
    snapshot_id: str = field(default_factory=lambda: str(uuid4()))
    registry_identity: str = "registry"
    authority_identity: str = ""
    tools: tuple[PlanningTool, ...] = ()
    eligible_names: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if not isinstance(self.snapshot_id, str) or not self.snapshot_id.strip():
            raise ValueError("snapshot_id inválido")
        if not isinstance(self.registry_identity, str) or not self.registry_identity.strip():
            raise ValueError("registry_identity inválida")
        if not isinstance(self.authority_identity, str) or not self.authority_identity.strip():
            raise ValueError("authority_identity inválida")
        tools = tuple(sorted(self.tools, key=lambda item: item.name))
        names = frozenset(self.eligible_names)
        if len({tool.name for tool in tools}) != len(tools):
            raise ValueError("planning snapshot contém nomes duplicados")
        if names != {tool.name for tool in tools}:
            raise ValueError("eligible_names deve corresponder exatamente às tools")
        object.__setattr__(self, "tools", tools)
        object.__setattr__(self, "eligible_names", names)


def build_planning_context(
    registry: Any,
    application_authority: ApplicationAuthoritySnapshot,
    task_authority: TaskAuthoritySnapshot | None,
    persona_restrictions: Iterable[str] | None,
) -> PlanningContextSnapshot:
    """Project registry descriptors without reading persistence or adapters."""

    registry_identity = _validate_binding(registry, application_authority)
    descriptors = tuple(registry.descriptors())
    effective = derive_effective_task_authority(task_authority, persona_restrictions)
    authority = application_authority.extension_grants
    projected = [
        planning_tool
        for descriptor in descriptors
        for planning_tool in (_project_descriptor(descriptor, authority, task_authority, effective),)
        if planning_tool is not None
    ]
    projected.sort(key=lambda item: item.name)
    return PlanningContextSnapshot(
        registry_identity=registry_identity.snapshot_id,
        authority_identity=application_authority.snapshot_id,
        tools=tuple(projected),
        eligible_names=frozenset(tool.name for tool in projected),
    )


def _planning_tool(descriptor: ToolDescriptor) -> PlanningTool:
    return PlanningTool(
        name=descriptor.name,
        description=descriptor.description,
        input_schema=descriptor.schema,
        required_capabilities=frozenset(descriptor.capabilities),
        origin_kind=descriptor.origin_kind,
        extension_id=descriptor.extension_id,
    )


def _validate_binding(
    registry: Any, application_authority: ApplicationAuthoritySnapshot
) -> RuntimeSnapshotIdentity:
    if not isinstance(application_authority, ApplicationAuthoritySnapshot):
        raise TypeError("application_authority inválida")
    if getattr(registry, "frozen", False) is not True:
        raise PlanningContextError("planning exige ToolRegistry congelado")
    registry_identity = getattr(registry, "runtime_identity", None)
    authority_identity = application_authority.runtime_identity
    if not isinstance(registry_identity, RuntimeSnapshotIdentity):
        raise PlanningContextError("registry sem runtime identity")
    if not isinstance(authority_identity, RuntimeSnapshotIdentity):
        raise PlanningContextError("authority sem runtime identity")
    if registry_identity != authority_identity:
        raise PlanningContextError("registry e authority pertencem a snapshots diferentes")
    return registry_identity


def _project_descriptor(
    descriptor: Any,
    grants_by_extension: Mapping[str, frozenset[str]],
    task_authority: TaskAuthoritySnapshot | None,
    effective: Any,
) -> PlanningTool | None:
    if not isinstance(descriptor, ToolDescriptor):
        raise PlanningContextError("registry contém descriptor inválido")
    if descriptor.origin_kind is ToolOriginKind.BUILTIN:
        if descriptor.adapter_id != "builtin" or descriptor.extension_id is not None:
            raise PlanningContextError("descriptor builtin contém origem incompatível")
        return _planning_tool(descriptor)
    if descriptor.origin_kind is not ToolOriginKind.EXTENSION:
        raise PlanningContextError("origem de tool desconhecida")
    return _planning_tool(descriptor) if _eligible_extension(
        descriptor, grants_by_extension, task_authority, effective
    ) else None


def _eligible_extension(
    descriptor: ToolDescriptor,
    grants_by_extension: Mapping[str, frozenset[str]],
    task_authority: TaskAuthoritySnapshot | None,
    effective: Any,
) -> bool:
    extension_id = descriptor.extension_id
    if not extension_id:
        raise PlanningContextError("tool de extension sem extension_id")
    grants = grants_by_extension.get(extension_id)
    if task_authority is None or grants is None or effective is None:
        return False
    required = frozenset(descriptor.capabilities)
    return required.issubset(grants) and required.issubset(effective.allowed_capabilities)


__all__ = [
    "PlanningContextError",
    "PlanningContextSnapshot",
    "PlanningTool",
    "build_planning_context",
]

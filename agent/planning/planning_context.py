"""Pure, safe projection of runtime tools into a planning snapshot."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Iterable, Mapping, cast
from uuid import uuid4

from agent.planning.planning_tool_normalization import normalize_planning_tool
from agent.planning.schema_safety import (
    MAX_SCHEMA_DEPTH,
    PlanningSchemaError,
    validate_planning_schema_shape,
)
from agent.runtime.argument_contract import validate_operation_arguments
from agent.runtime.schema_validation import normalize_argument_schema, validate_schema_arguments
from agent.tools.authority import (
    ApplicationAuthoritySnapshot,
    TaskAuthoritySnapshot,
    derive_effective_task_authority,
)
from agent.tools.contracts import (
    CancellationSafetyMode,
    ToolDescriptor,
    ToolOriginKind,
    thaw_json_like,
)
from agent.tools.runtime_identity import RuntimeSnapshotIdentity

if TYPE_CHECKING:
    from agent.planning.presentation import PlanningPresentationSnapshot


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
    category: str = "EXECUTE"
    cost: int = 5
    timeout_seconds: int | None = None
    cacheable: bool = False
    idempotent: bool = False
    supports_cancellation: bool = False
    cancellation_safety: CancellationSafetyMode = CancellationSafetyMode.UNSUPPORTED
    argument_provenance: Mapping[str, frozenset[str]] = field(default_factory=dict)
    result_data_schema: Mapping[str, Any] | None = field(default=None, kw_only=True)
    argument_validator: Callable[..., None] | None = field(default=None, kw_only=True)
    usage_examples: tuple[Mapping[str, Any], ...] = field(
        default_factory=tuple,
        kw_only=True,
    )
    def __post_init__(self) -> None:
        normalize_planning_tool(self, PlanningContextError)

    def __getattribute__(self, name: str) -> Any:
        if name == "input_schema":
            return thaw_json_like(object.__getattribute__(self, "input_schema"))
        if name == "result_data_schema":
            return thaw_json_like(object.__getattribute__(self, "result_data_schema"))
        return object.__getattribute__(self, name)

def validate_planning_tool_arguments(
    descriptor: Any, args: Mapping[str, Any], bound_fields: set[str] | None = None
) -> None:
    """Validate JSON arguments against canonical planning metadata."""

    schema = getattr(descriptor, "input_schema", None)
    if schema is None:
        schema = getattr(descriptor, "schema", None)
    if schema is None:
        return
    try:
        validate_planning_schema_shape(schema)
    except PlanningSchemaError:
        raise
    if not isinstance(args, dict):
        raise ValueError("arguments must be a JSON object")
    effective_schema = normalize_argument_schema(schema)
    validate_schema_arguments(
        effective_schema,
        args,
        bound_fields=bound_fields,
        planning=True,
    )
    validate_operation_arguments(
        descriptor,
        args,
        bound_fields=bound_fields,
        planning=True,
    )

@dataclass(frozen=True, slots=True)
class PlanningContextSnapshot:
    snapshot_id: str = field(default_factory=lambda: str(uuid4()))
    registry_identity: str = "registry"
    authority_identity: str = ""
    tools: tuple[PlanningTool, ...] = ()
    eligible_names: frozenset[str] = field(default_factory=frozenset)
    runtime_identity: RuntimeSnapshotIdentity | None = None
    allowed_capabilities: frozenset[str] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.snapshot_id, str) or not self.snapshot_id.strip():
            raise ValueError("snapshot_id inválido")
        if not isinstance(self.registry_identity, str) or not self.registry_identity.strip():
            raise ValueError("registry_identity inválida")
        if not isinstance(self.authority_identity, str) or not self.authority_identity.strip():
            raise ValueError("authority_identity inválida")
        if not isinstance(self.runtime_identity, RuntimeSnapshotIdentity):
            raise PlanningContextError("planning snapshot requer runtime identity")
        if self.runtime_identity.snapshot_id != self.registry_identity:
            raise PlanningContextError("runtime identity diverge da identidade do registry")
        if self.allowed_capabilities is not None:
            object.__setattr__(self, "allowed_capabilities", frozenset(self.allowed_capabilities))
        tools = tuple(sorted(self.tools, key=lambda item: item.name))
        names = frozenset(self.eligible_names)
        if len({tool.name for tool in tools}) != len(tools):
            raise ValueError("planning snapshot contém nomes duplicados")
        if names != {tool.name for tool in tools}:
            raise ValueError("eligible_names deve corresponder exatamente às tools")
        object.__setattr__(self, "tools", tools)
        object.__setattr__(self, "eligible_names", names)
    def present(
        self,
        planner_kind: str,
        visible_names: Iterable[str] | None = None,
    ) -> "PlanningPresentationSnapshot":
        """Create a deterministic planner view without accessing runtime objects."""

        from agent.planning.presentation import PlanningPresentationSnapshot

        names = self.eligible_names if visible_names is None else frozenset(visible_names)
        if not names.issubset(self.eligible_names):
            raise PlanningContextError("visibilidade de planning excede as tools elegíveis")
        tools = tuple(tool for tool in self.tools if tool.name in names)
        return PlanningPresentationSnapshot(
            planning_context_id=self.snapshot_id,
            planner_kind=planner_kind,
            tools=tools,
            presented_names=frozenset(tool.name for tool in tools),
            runtime_identity=self.runtime_identity,
        )

    def resolve_view(
        self,
        planner_kind: str,
        active_names: Iterable[str] | None = None,
    ) -> "PlanningPresentationSnapshot":
        """Resolve the one planner view for active names and eligible tools."""

        active = frozenset(active_names or ())
        visible = self.eligible_names if not active else active & self.eligible_names
        return self.present(planner_kind, visible)

    @property
    def workspace_id(self) -> str:
        identity = self.runtime_identity
        if identity is None:
            raise PlanningContextError("planning snapshot sem runtime identity")
        return cast(str, identity.workspace_id)


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
    context_capabilities = (
        effective.allowed_capabilities
        if effective is not None
        else (frozenset(persona_restrictions) if persona_restrictions is not None else None)
    )
    return PlanningContextSnapshot(
        registry_identity=registry_identity.snapshot_id,
        authority_identity=application_authority.snapshot_id,
        tools=tuple(projected),
        eligible_names=frozenset(tool.name for tool in projected),
        runtime_identity=registry_identity,
        allowed_capabilities=context_capabilities,
    )


def _planning_tool(descriptor: ToolDescriptor) -> PlanningTool:
    return PlanningTool(
        name=descriptor.name,
        description=descriptor.description,
        input_schema=descriptor.schema,
        required_capabilities=frozenset(descriptor.capabilities),
        origin_kind=descriptor.origin_kind,
        extension_id=descriptor.extension_id,
        category=descriptor.category,
        cost=descriptor.cost,
        timeout_seconds=descriptor.timeout_seconds,
        cacheable=descriptor.cacheable,
        idempotent=descriptor.idempotent,
        supports_cancellation=descriptor.supports_cancellation,
        cancellation_safety=descriptor.cancellation_safety,
        argument_provenance=descriptor.argument_provenance,
        result_data_schema=descriptor.result_data_schema,
        argument_validator=descriptor.argument_validator,
        usage_examples=descriptor.usage_examples,
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
    "MAX_SCHEMA_DEPTH",
    "PlanningContextSnapshot",
    "PlanningTool",
    "build_planning_context",
    "validate_planning_tool_arguments",
]

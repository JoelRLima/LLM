"""Descritores canônicos de skills e seus efeitos observáveis."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, Optional, Protocol

from agent.planning.schema_safety import PlanningSchemaError, validate_schema_depth
from agent.tools.contracts import freeze_json_like, thaw_json_like
from agent.tools.provenance import ArgumentOrigin


class SkillCapability(str, Enum):
    READ = "read"
    WRITE = "write"
    PROCESS = "process"
    NETWORK = "network"
    MEMORY = "memory"
    ANALYZE = "analyze"
    VCS_READ = "vcs_read"
    VCS_WRITE = "vcs_write"
    PACKAGE_INSTALL = "package_install"
    VALIDATE = "validate"


@dataclass(frozen=True)
class ResourceIntent:
    resource: str
    write: bool = False


class SkillLike(Protocol):
    name: str
    description: str

    def get_schema(self) -> Dict[str, Any]:
        ...

    def execute(self, args: Dict[str, Any]) -> Dict[str, Any]:
        ...


_RESULT_DATA_SCHEMA_TYPES = frozenset(
    {"array", "boolean", "integer", "null", "number", "object", "string"}
)
_RESULT_DATA_SCHEMA_KEYS = frozenset({"type", "properties", "items"})


def validate_result_data_schema(value: Any) -> None:
    """Validate bounded, data-only result structure metadata."""

    if not isinstance(value, Mapping):
        raise TypeError("result_data_schema deve ser um mapping JSON-like")
    try:
        validate_schema_depth(value, max_depth=16)
    except PlanningSchemaError as exc:
        raise ValueError("result_data_schema excede o limite estrutural") from exc
    pending: list[Mapping[str, Any]] = [value]
    for _ in range(256):
        if not pending:
            return
        pending.extend(_result_schema_children(pending.pop()))
    raise ValueError("result_data_schema excede o limite de elementos")


def freeze_result_data_schema(value: Mapping[str, Any] | None) -> Any:
    if value is None:
        return None
    validate_result_data_schema(value)
    return freeze_json_like(dict(value))


def _result_schema_children(node: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    if any(type(key) is not str for key in node):
        raise TypeError("result_data_schema requer chaves textuais")
    if set(node) - _RESULT_DATA_SCHEMA_KEYS:
        raise ValueError("result_data_schema contém campos não suportados")
    schema_type = node.get("type")
    if schema_type is not None and (
        type(schema_type) is not str or schema_type not in _RESULT_DATA_SCHEMA_TYPES
    ):
        raise ValueError("result_data_schema.type contém valor não suportado")
    children: list[Mapping[str, Any]] = []
    if "properties" in node:
        children.extend(_result_schema_properties(node["properties"], schema_type))
    if "items" in node:
        children.append(_result_schema_items(node["items"], schema_type))
    return children


def _result_schema_properties(value: Any, schema_type: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, Mapping):
        raise TypeError("result_data_schema.properties deve ser um mapping")
    if schema_type is not None and schema_type != "object":
        raise ValueError("result_data_schema.properties requer type object")
    children: list[Mapping[str, Any]] = []
    for name, child in value.items():
        if type(name) is not str or not isinstance(child, Mapping):
            raise TypeError("result_data_schema.properties contém schema inválido")
        children.append(child)
    return children


def _result_schema_items(value: Any, schema_type: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError("result_data_schema.items deve ser um objeto de schema")
    if schema_type is not None and schema_type != "array":
        raise ValueError("result_data_schema.items requer type array")
    return value


def result_data_schema_for_contract(contract: Any) -> Mapping[str, Any] | None:
    if contract is None:
        return None
    schema = getattr(contract, "result_data_schema", None)
    if isinstance(schema, Mapping):
        return schema
    spec = getattr(contract, "spec", None)
    schema = getattr(spec, "result_data_schema", None)
    return schema if isinstance(schema, Mapping) else None


def target_schema_for_contract(contract: Any, target: str) -> Mapping[str, Any] | None:
    if contract is None:
        return None
    schema = getattr(contract, "input_schema", None) or getattr(contract, "schema", None)
    if not isinstance(schema, Mapping):
        return None
    properties = schema.get("properties")
    if not isinstance(properties, Mapping):
        properties = {
            key: value
            for key, value in schema.items()
            if key not in {"type", "required", "properties", "additionalProperties"}
        }
    target_schema = properties.get(target)
    return target_schema if isinstance(target_schema, Mapping) else None


@dataclass(frozen=True)
class SkillSpec:
    """Fonte canônica para construção, custo, risco e agendamento."""

    module: str
    class_name: str
    name: str
    kwargs: Dict[str, Any] = field(default_factory=dict)
    capabilities: frozenset[SkillCapability] = frozenset()
    cost: int = 5
    cacheable: bool = False
    idempotent: bool = False
    timeout_seconds: Optional[int] = None
    category: str = "EXECUTE"
    public_invocation_fields: frozenset[str] = frozenset()
    argument_provenance: Mapping[str, frozenset[str | ArgumentOrigin]] = field(
        default_factory=dict
    )
    result_data_schema: Mapping[str, Any] | None = field(default=None, kw_only=True)

    def __post_init__(self) -> None:
        object.__setattr__(self, "result_data_schema", freeze_result_data_schema(self.result_data_schema))

    def __getattribute__(self, name: str) -> Any:
        if name == "result_data_schema":
            snapshot = object.__getattribute__(self, "result_data_schema")
            return None if snapshot is None else thaw_json_like(snapshot)
        return object.__getattribute__(self, name)

    @property
    def side_effects(self) -> bool:
        return bool(
            self.capabilities
            & {
                SkillCapability.WRITE,
                SkillCapability.PROCESS,
                SkillCapability.NETWORK,
                SkillCapability.VCS_WRITE,
                SkillCapability.PACKAGE_INSTALL,
                SkillCapability.VALIDATE,
            }
        )


ResourceResolver = Callable[[Dict[str, Any]], tuple[ResourceIntent, ...]]


@dataclass(frozen=True)
class SkillDescriptor:
    spec: SkillSpec
    skill: SkillLike
    resource_resolver: Optional[ResourceResolver] = None

    @property
    def name(self) -> str:
        return self.spec.name

    @property
    def schema(self) -> Dict[str, Any]:
        return self.skill.get_schema()

    def resources(self, args: Dict[str, Any]) -> tuple[ResourceIntent, ...]:
        if self.resource_resolver:
            return self.resource_resolver(args)
        paths = []
        for key in ("file_path", "target", "path", "directory"):
            value = args.get(key)
            if isinstance(value, str) and value:
                paths.append(value.replace("\\", "/"))
        writes = SkillCapability.WRITE in self.spec.capabilities
        return tuple(ResourceIntent(path, write=writes) for path in dict.fromkeys(paths))

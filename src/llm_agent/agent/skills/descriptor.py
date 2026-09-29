"""Descritores canônicos de skills e seus efeitos observáveis."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Protocol

from llm_agent.agent.capabilities import Capability, canonical_capabilities
from llm_agent.agent.operation.schema import (
    freeze_result_data_schema,
    result_data_schema_for_contract,
    target_schema_for_contract,
    validate_result_data_schema,
)
from llm_agent.agent.operation.spec import SkillSpec
from llm_agent.agent.operation.usage_examples import normalize_usage_examples
from llm_agent.agent.resources.contracts import (
    ResourceAccess,
    ResourceMode,
    ResourceProvenance,
    normalize_resource_id,
)

# Compatibility name retained at the descriptor boundary.  The vocabulary
# itself is owned by ``llm_agent.agent.capabilities`` so planning and execution cannot
# silently grow divergent capability universes.
SkillCapability = Capability

__all__ = [
    "ResourceIntent",
    "ResourceResolver",
    "SkillCapability",
    "SkillDescriptor",
    "SkillLike",
    "SkillSpec",
    "freeze_result_data_schema",
    "result_data_schema_for_contract",
    "target_schema_for_contract",
    "validate_result_data_schema",
]


@dataclass(frozen=True)
class ResourceIntent:
    resource: str
    write: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "resource", normalize_resource_id(self.resource))

    @property
    def name(self) -> str:
        return self.resource

    @property
    def mode(self) -> ResourceMode:
        return ResourceMode.WRITE if self.write else ResourceMode.READ

    def as_access(self) -> ResourceAccess:
        return ResourceAccess(
            self.resource,
            self.mode,
            ResourceProvenance.MODEL_DECLARED,
        )


class SkillLike(Protocol):
    name: str
    description: str

    def get_schema(self) -> Dict[str, Any]:
        ...

    def execute(self, args: Dict[str, Any]) -> Dict[str, Any]:
        ...

    def validate_arguments(
        self,
        args: Mapping[str, Any],
        *,
        bound_fields: frozenset[str] = frozenset(),
        planning: bool = False,
    ) -> None:
        ...


ResourceResolver = Callable[[Dict[str, Any]], tuple[ResourceIntent, ...]]


@dataclass(frozen=True)
class SkillDescriptor:
    spec: SkillSpec
    skill: SkillLike
    resource_resolver: Optional[ResourceResolver] = None

    def __post_init__(self) -> None:
        normalize_usage_examples(
            self.spec.usage_examples,
            schema=self.schema,
            argument_validator=getattr(self.skill, "validate_arguments", None),
        )

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
        writes = Capability.WRITE in canonical_capabilities(self.spec.capabilities)
        return tuple(ResourceIntent(path, write=writes) for path in dict.fromkeys(paths))

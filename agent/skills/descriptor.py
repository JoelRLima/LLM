"""Descritores canônicos de skills e seus efeitos observáveis."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, Optional, Protocol


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

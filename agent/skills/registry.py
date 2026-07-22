"""Registro tipado e construção explícita de skills."""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, Optional, cast

from agent.skills.catalog import BUILTIN_SKILL_SPECS
from agent.skills.descriptor import SkillDescriptor, SkillLike, SkillSpec


class SkillRegistry:
    def __init__(self) -> None:
        self._descriptors: Dict[str, SkillDescriptor] = {}

    def register(self, descriptor: SkillDescriptor) -> None:
        if descriptor.name in self._descriptors:
            raise ValueError(f"Skill duplicada: {descriptor.name}")
        if descriptor.skill.name != descriptor.spec.name:
            raise ValueError(
                f"Skill '{descriptor.skill.name}' diverge do spec '{descriptor.spec.name}'."
            )
        self._descriptors[descriptor.name] = descriptor

    def descriptor(self, name: str) -> SkillDescriptor:
        try:
            return self._descriptors[name]
        except KeyError as exc:
            raise KeyError(f"Skill não registrada: {name}") from exc

    def skill(self, name: str) -> SkillLike:
        return self.descriptor(name).skill

    def names(self) -> tuple[str, ...]:
        return tuple(self._descriptors)

    def skills(self) -> tuple[SkillLike, ...]:
        return tuple(descriptor.skill for descriptor in self._descriptors.values())

    def as_dict(self) -> Dict[str, SkillLike]:
        return {name: descriptor.skill for name, descriptor in self._descriptors.items()}

    def __iter__(self) -> Iterator[SkillDescriptor]:
        return iter(self._descriptors.values())


def _instantiate(spec: SkillSpec, overrides: Dict[str, Any]) -> SkillLike:
    module = importlib.import_module(spec.module)
    cls = getattr(module, spec.class_name)
    kwargs = dict(spec.kwargs)
    kwargs.update(overrides)
    skill = cls(**kwargs)
    return cast(SkillLike, skill)


def build_builtin_registry(
    *,
    base_dir: str | Path = ".",
    orchestrator: Any = None,
    model_gateway: Any = None,
    config: Optional[Dict[str, Any]] = None,
    specs: Iterable[SkillSpec] = BUILTIN_SKILL_SPECS,
) -> SkillRegistry:
    registry = SkillRegistry()
    for spec in specs:
        overrides: Dict[str, Any] = {}
        if "base_dir" in spec.kwargs:
            overrides["base_dir"] = str(base_dir)
        if "orchestrator" in spec.kwargs:
            overrides["orchestrator"] = orchestrator
        if "model_gateway" in spec.kwargs:
            overrides["model_gateway"] = model_gateway
        if "config" in spec.kwargs:
            overrides["config"] = config or {}
        skill = _instantiate(spec, overrides)
        registry.register(SkillDescriptor(spec=spec, skill=skill))
    return registry

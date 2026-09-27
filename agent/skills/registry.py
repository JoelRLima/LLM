"""Registro tipado e construção explícita de skills."""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, Optional, cast

from agent.operation.catalog import BUILTIN_SKILL_SPECS
from agent.operation.spec import SkillSpec
from agent.skills.descriptor import SkillDescriptor, SkillLike


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
    scratch_dir: str | Path | None = None,
    session: Any = None,
    memory: Any = None,
    workspace_manager: Any = None,
    # Kept as a source-compatible direct-composition input.  Builtin specs
    # no longer declare this broad dependency; it is reduced to the exact
    # capability requested by each named spec below.
    orchestrator: Any = None,
    model_gateway: Any = None,
    config: Optional[Dict[str, Any]] = None,
    approval_policy: Any = None,
    specs: Iterable[SkillSpec] = BUILTIN_SKILL_SPECS,
) -> SkillRegistry:
    registry = SkillRegistry()
    for spec in specs:
        overrides: Dict[str, Any] = {}
        if "base_dir" in spec.kwargs:
            overrides["base_dir"] = str(base_dir)
        if "scratch_dir" in spec.kwargs:
            overrides["scratch_dir"] = (
                str(scratch_dir) if scratch_dir is not None else None
            )
        if "session" in spec.kwargs:
            overrides["session"] = (
                session
                if session is not None
                else getattr(orchestrator, "session", None)
            )
        if "memory" in spec.kwargs:
            legacy_state = getattr(orchestrator, "agent_state", None)
            overrides["memory"] = (
                memory
                if memory is not None
                else getattr(legacy_state, "memory", None)
            )
        if "workspace_manager" in spec.kwargs:
            overrides["workspace_manager"] = (
                workspace_manager
                if workspace_manager is not None
                else getattr(orchestrator, "workspace", None)
            )
        if "orchestrator" in spec.kwargs:
            # Compatibility for an explicitly custom legacy SkillSpec only.
            # The canonical builtin catalog has no such declaration.
            overrides["orchestrator"] = orchestrator
        if "model_gateway" in spec.kwargs:
            overrides["model_gateway"] = model_gateway
        if "config" in spec.kwargs:
            overrides["config"] = config or {}
        if "approval_policy" in spec.kwargs:
            overrides["approval_policy"] = approval_policy
        skill = _instantiate(spec, overrides)
        registry.register(SkillDescriptor(spec=spec, skill=skill))
    return registry


def bind_runtime_skill_dependencies(
    registry: SkillRegistry,
    *,
    session: Any,
    memory: Any,
    workspace_manager: Any | None,
) -> None:
    """Bind the three known runtime capabilities without scanning the registry."""

    names = registry.names()
    if "summarize" in names:
        descriptor = registry.descriptor("summarize")
        if "session" in descriptor.spec.kwargs:
            cast(Any, descriptor.skill).bind_session(session)
    if "session_memory" in names:
        descriptor = registry.descriptor("session_memory")
        if "memory" in descriptor.spec.kwargs:
            cast(Any, descriptor.skill).bind_memory(memory)
    if "file_writer" in names:
        descriptor = registry.descriptor("file_writer")
        if "workspace_manager" in descriptor.spec.kwargs:
            if workspace_manager is None:
                raise ValueError("file_writer requires an explicit workspace manager")
            cast(Any, descriptor.skill).bind_workspace_manager(workspace_manager)

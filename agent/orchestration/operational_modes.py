"""Session-local operational mode projection over canonical tool policy."""

from __future__ import annotations

from typing import Any

from agent.skills.policy import builtin_skills_for_persona
from agent.tools.authority import OperationalMode, operational_mode_capabilities


class OperationalModeMixin:
    _operational_mode: OperationalMode | None

    @property
    def operational_mode(self) -> OperationalMode | None:
        return self._operational_mode

    @property
    def operational_mode_label(self) -> str:
        return self._operational_mode.display_name if self._operational_mode is not None else "FULL"

    def set_operational_mode(self, mode: OperationalMode) -> None:
        set_operational_mode(self, mode)

    def mode_allows(self, capabilities: set[str] | frozenset[str]) -> bool:
        return mode_allows(self, capabilities)


class ApplicationOperationalModeMixin:
    orchestrator: Any

    @property
    def operational_mode(self) -> OperationalMode | None:
        return getattr(self.orchestrator, "operational_mode", None)

    def set_operational_mode(self, mode: OperationalMode) -> None:
        self.orchestrator.set_operational_mode(mode)


def refresh_capability_projection(orchestrator: Any) -> None:
    persona = orchestrator._persona_allowed_capabilities
    task_authority = getattr(orchestrator, "task_authority", None)
    task_capabilities = (
        frozenset(task_authority.allowed_capabilities)
        if task_authority is not None
        else None
    )
    granted = persona
    if task_capabilities is not None:
        granted = task_capabilities if granted is None else granted & task_capabilities
    ceiling = operational_mode_capabilities(orchestrator._operational_mode) if orchestrator._operational_mode else None
    if granted is None:
        orchestrator.allowed_capabilities = frozenset() if ceiling is None else ceiling
    else:
        orchestrator.allowed_capabilities = granted if ceiling is None else granted & ceiling
    if persona is None or orchestrator.tool_registry is None:
        return
    active = builtin_skills_for_persona(
        getattr(orchestrator, "current_persona", "general"), registry=orchestrator.tool_registry
    )
    active = [
        name for name in active
        if frozenset(orchestrator.tool_registry.descriptor(name).capabilities).issubset(
            orchestrator.allowed_capabilities
        )
    ]
    orchestrator.active_skills = active


def set_operational_mode(orchestrator: Any, mode: OperationalMode) -> None:
    if not isinstance(mode, OperationalMode):
        raise TypeError("modo operacional invalido")
    orchestrator._operational_mode = mode
    refresh_capability_projection(orchestrator)
    orchestrator._create_planning_context()
    persona_prompt = getattr(orchestrator, "current_persona_prompt", None)
    if persona_prompt is not None:
        orchestrator._cached_base_prompt = orchestrator.context_manager.build_base_system_prompt(
            persona_prompt, orchestrator._build_tools_description(compact=False)
        )
    gateway = orchestrator.tool_invocation_gateway
    if gateway is not None:
        gateway.set_capability_ceiling(operational_mode_capabilities(mode), mode=mode.display_name)


def mode_allows(orchestrator: Any, capabilities: set[str] | frozenset[str]) -> bool:
    ceiling = operational_mode_capabilities(orchestrator._operational_mode) if orchestrator._operational_mode else None
    return ceiling is None or frozenset(capabilities).issubset(ceiling)

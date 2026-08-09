"""Compatibility composition for direct Orchestrator construction."""

from __future__ import annotations

from typing import Any

from agent.approval import RequireExplicitApproval
from agent.skills.catalog import BUILTIN_SPEC_BY_NAME
from agent.skills.descriptor import SkillDescriptor
from agent.skills.registry import SkillRegistry
from agent.tools.builtin_adapter import BuiltinToolAdapter
from agent.tools.invocation_gateway import ToolInvocationGateway
from agent.tools.tool_registry import ToolRegistry


def install_compatibility_gateway(
    orchestrator: Any,
    selected_skills: list[Any],
    *,
    skill_registry: SkillRegistry | None = None,
) -> bool:
    """Install canonical enforcement when real skill metadata is available."""

    compatibility_skills = skill_registry or SkillRegistry()
    if skill_registry is None:
        for skill in selected_skills:
            spec = BUILTIN_SPEC_BY_NAME.get(str(getattr(skill, "name", "")))
            if spec is None:
                return False
            compatibility_skills.register(SkillDescriptor(spec=spec, skill=skill))
    registry = ToolRegistry()
    registry.register_adapter(BuiltinToolAdapter(compatibility_skills))
    registry.freeze()
    orchestrator.tool_registry = registry
    orchestrator.tool_invocation_gateway = ToolInvocationGateway(
        registry,
        approval_port=RequireExplicitApproval(),
        event_emitter=orchestrator._emit,
        state_recorder=lambda name, args, result: orchestrator.agent_state.record_tool_result(
            name, args, result.to_legacy_dict()
        ),
    )
    return True


__all__ = ["install_compatibility_gateway"]

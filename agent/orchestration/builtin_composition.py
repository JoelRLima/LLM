"""Canonical built-in skill composition for direct orchestrator construction."""

from __future__ import annotations

from typing import Any

from agent.approval import RequireExplicitApproval
from agent.skills.catalog import BUILTIN_SPEC_BY_NAME
from agent.skills.descriptor import SkillDescriptor
from agent.skills.registry import SkillRegistry
from agent.tools.builtin_adapter import BuiltinToolAdapter
from agent.tools.invocation_gateway import ToolInvocationGateway
from agent.tools.tool_registry import ToolRegistry


def install_builtin_gateway(
    orchestrator: Any,
    selected_skills: list[Any],
    *,
    skill_registry: SkillRegistry | None = None,
) -> bool:
    """Install the canonical gateway for a direct orchestrator composition."""

    selected_registry = skill_registry or SkillRegistry()
    if skill_registry is None:
        for skill in selected_skills:
            spec = BUILTIN_SPEC_BY_NAME.get(str(getattr(skill, "name", "")))
            if spec is None:
                return False
            selected_registry.register(SkillDescriptor(spec=spec, skill=skill))
    registry = ToolRegistry()
    registry.register_adapter(BuiltinToolAdapter(selected_registry))
    registry.freeze()
    orchestrator.tool_registry = registry
    orchestrator.tool_invocation_gateway = ToolInvocationGateway(
        registry,
        budget_ledger=orchestrator.task_budget,
        approval_port=RequireExplicitApproval(),
        event_dispatcher=orchestrator.event_dispatcher,
        correlation_provider=lambda: orchestrator.run_correlation,
        event_fields_provider=lambda: {
            "plan_id": getattr(orchestrator.agent_state, "plan_identity", None),
            "step_id": getattr(orchestrator.agent_state, "current_step_id", None),
        },
        correlated_state_recorder=lambda name, args, result, correlation: orchestrator.agent_state.record_tool_result(
            name, args, result, correlation=correlation
        ),
        incident_recorder=orchestrator.agent_state.record_execution_incident,
    )
    return True


__all__ = ["install_builtin_gateway"]

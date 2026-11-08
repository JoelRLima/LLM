"""Conservative route selection over already-admitted runtime state."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from llm_agent.agent.planning.task_semantics import TaskSemantics
from llm_agent.agent.runtime.execution_profile import ExecutionProfile, resolve_execution_profile
from llm_agent.agent.runtime.task_directives import (
    READ_CAPABILITY_CEILING,
    DeliberationProfile,
    TaskDirective,
    TaskRunDirective,
)


def use_compact_read_route(orchestrator: Any, inputs: Any) -> bool:
    """Skip full-plan generation only for a fresh bounded read-only task.

    Called after task-definition, intent admission and specialized routes. No
    intent is inferred here and the existing one-step execution gateway retains
    every permission, argument, cancellation and completion check.
    """
    config = getattr(getattr(orchestrator, "session", None), "config", {})
    if not isinstance(config, Mapping):
        return False
    if resolve_execution_profile(config) is not ExecutionProfile.COMPACT:
        return False
    if inputs.resumed or len(inputs.objective) > 220:
        return False
    directive = getattr(orchestrator.agent_state, "task_run_directive", None)
    if not _eligible_directive(directive):
        return False
    capabilities = getattr(orchestrator, "allowed_capabilities", None)
    if not isinstance(capabilities, (set, frozenset)) or not capabilities:
        return False
    if not capabilities.issubset(READ_CAPABILITY_CEILING):
        return False
    semantics = getattr(orchestrator.agent_state, "task_semantics", None)
    return (
        isinstance(semantics, TaskSemantics)
        and semantics.intent.original_objective == inputs.objective
        and not semantics.requested_effects
        and len(semantics.obligations) <= 2
    )


def _eligible_directive(directive: Any) -> bool:
    return (
        isinstance(directive, TaskRunDirective)
        and directive.directive in {TaskDirective.AUTO, TaskDirective.READ}
        and directive.deliberation_profile in {DeliberationProfile.ECONOMY, DeliberationProfile.NORMAL}
    )

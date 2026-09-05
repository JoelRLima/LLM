"""Canonical operational evidence rules for effect obligations."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent.planning.task_semantics_code_support import (
    code_outcome_seal_candidate,
    verified_code_abstention_proves_waiver,
    verified_code_abstention_scope_covers_pending_write,
)
from agent.planning.task_semantics_effect_support import (
    _invocation_semantics,
    effect_observation_proves_terminal,
    tool_capabilities,
)
from agent.reporting.observation_evidence import result_executed
from agent.resources.contracts import (
    WORKSPACE_RESOURCE,
    ResourceAccess,
    ResourceMode,
    ResourceProvenance,
    normalize_resource_id,
)
from agent.runtime.mutation_evidence import project_mutation_evidence


def observed_effect_kinds(authority: Any, observation: Mapping[str, Any]) -> tuple[str, ...]:
    """Return only durable effects proved by one trusted observation."""

    result = observation.get("result")
    if not isinstance(result, Mapping):
        return ()
    semantics = _invocation_semantics(authority, observation)
    if semantics is None or result_executed(result) is not True:
        return ()
    if not project_mutation_evidence(result).persisted_mutation:
        return ()
    return tuple(semantics.durable_effects)


def observed_effect_accesses(
    authority: Any,
    observation: Mapping[str, Any],
) -> tuple[tuple[str, ResourceAccess], ...]:
    """Project durable effects and their observed mutation scope.

    The operation resolver describes what a registered invocation can do;
    artifact evidence supplies the physical footprint.  If a mutating result
    proves persistence but omits its footprint, widen the observed scope to
    the workspace instead of trusting the invocation's requested target.
    """

    result = observation.get("result")
    if not isinstance(result, Mapping):
        return ()
    semantics = _invocation_semantics(authority, observation)
    if semantics is None or result_executed(result) is not True:
        return ()
    evidence = project_mutation_evidence(result)
    # An attempted write that failed before committing is not an observed
    # durable effect.  A committed-then-rolled-back write remains an
    # occurrence and is still relevant to prohibited-effect containment.
    if not (evidence.occurred or evidence.survives):
        return ()
    effects = tuple(semantics.durable_effects)
    if not effects:
        return ()

    raw_resources = tuple(
        dict.fromkeys(
            normalize_resource_id(path)
            for path in (
                *evidence.affected_resources,
                *evidence.mutated_resources,
                *evidence.surviving_resources,
            )
            if isinstance(path, str) and path.strip()
        )
    )
    if raw_resources:
        accesses = tuple(
            ResourceAccess(path, ResourceMode.WRITE, ResourceProvenance.OBSERVED_MUTATION)
            for path in raw_resources
        )
    elif "memory_write" in effects:
        accesses = (
            ResourceAccess(
                "memory",
                ResourceMode.WRITE,
                ResourceProvenance.OBSERVED_MUTATION,
            ),
        )
    else:
        accesses = (
            ResourceAccess(
                WORKSPACE_RESOURCE,
                ResourceMode.WRITE,
                ResourceProvenance.OBSERVED_MUTATION,
            ),
        )
    return tuple((effect, access) for effect in effects for access in accesses)


__all__ = [
    "code_outcome_seal_candidate",
    "effect_observation_proves_terminal",
    "observed_effect_accesses",
    "observed_effect_kinds",
    "tool_capabilities",
    "verified_code_abstention_proves_waiver",
    "verified_code_abstention_scope_covers_pending_write",
]

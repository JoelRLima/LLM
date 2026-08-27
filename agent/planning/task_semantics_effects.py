"""Canonical operational evidence rules for effect obligations."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent.planning.task_semantics_types import ObligationStatus
from agent.reporting.observation_evidence import (
    result_executed,
    result_has_data,
    result_is_failed,
    result_is_successful,
)
from agent.resources.contracts import (
    WORKSPACE_RESOURCE,
    ResourceAccess,
    ResourceMode,
    ResourceProvenance,
    normalize_resource_id,
)
from agent.runtime.mutation_evidence import project_mutation_evidence
from agent.tools.invocation_semantics import resolve_invocation_semantics


def tool_capabilities(authority: Any, tool_name: str) -> frozenset[str] | None:
    """Return known capabilities; ``None`` means the authority is unknown."""

    registry = getattr(authority, "tool_registry", None)
    if registry is None and callable(getattr(authority, "descriptor", None)):
        registry = authority
    if registry is None:
        return None
    try:
        descriptor = registry.descriptor(tool_name)
        raw_capabilities = getattr(descriptor, "capabilities", None)
        if raw_capabilities is None or isinstance(raw_capabilities, (str, bytes, Mapping)):
            return None
        values = tuple(raw_capabilities)
    except Exception:
        return None
    if any(type(value) is not str or not value.strip() for value in values):
        return None
    return frozenset(values)


def _invocation_semantics(authority: Any, observation: Mapping[str, Any]) -> Any | None:
    registry = getattr(authority, "tool_registry", None)
    if registry is None and callable(getattr(authority, "descriptor", None)):
        registry = authority
    if registry is None:
        return None
    tool = str(observation.get("tool") or "")
    try:
        descriptor = registry.descriptor(tool)
    except Exception:
        return None
    args = observation.get("args")
    return resolve_invocation_semantics(
        descriptor,
        args if isinstance(args, Mapping) else {},
    )


def effect_observation_proves_terminal(
    authority: Any,
    status: ObligationStatus,
    observation: Mapping[str, Any],
) -> bool:
    """Prove one effect transition from live authority and recorded result.

    The result and the capability decision are deliberately kept together here.
    Callers must not turn an arbitrary history entry into an effect by checking
    only one of those facts.
    """

    tool = str(observation.get("tool") or "")
    result = observation.get("result")
    if not isinstance(result, Mapping):
        return False
    capabilities = tool_capabilities(authority, tool)
    semantics = _invocation_semantics(authority, observation)
    if capabilities is None or semantics is None:
        return False

    if status is ObligationStatus.SATISFIED:
        return (
            result_executed(result) is True
            and bool(semantics.durable_effects)
            and project_mutation_evidence(result).persisted_mutation
        )

    if status is ObligationStatus.WAIVED:
        return (
            result_executed(result) is True
            and result_is_successful(result)
            and result_has_data(result)
            and semantics.read_only
        )

    if status is ObligationStatus.BLOCKED:
        return (
            semantics.may_mutate
            and result_is_failed(result)
            and not project_mutation_evidence(result).persisted_mutation
        )

    return False


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
    "effect_observation_proves_terminal",
    "observed_effect_accesses",
    "observed_effect_kinds",
    "tool_capabilities",
]

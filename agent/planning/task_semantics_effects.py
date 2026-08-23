"""Canonical operational evidence rules for effect obligations."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent.planning.operational_constants import WRITE_CAPABILITIES
from agent.planning.task_semantics_types import ObligationStatus
from agent.reporting.observation_evidence import (
    artifact_metadata,
    project_artifact_evidence,
    result_executed,
    result_has_data,
    result_is_failed,
    result_is_successful,
)

_MEMORY_CAPABILITY = "memory"


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
    if capabilities is None:
        return False

    if status is ObligationStatus.SATISFIED:
        return (
            result_executed(result) is True
            and (
                bool(capabilities & WRITE_CAPABILITIES)
                or _is_memory_write_observation(tool, capabilities, observation)
            )
            and project_artifact_evidence(result).persisted_mutation
        )

    if status is ObligationStatus.WAIVED:
        return (
            result_executed(result) is True
            and result_is_successful(result)
            and result_has_data(result)
            and not bool(capabilities & (WRITE_CAPABILITIES | {_MEMORY_CAPABILITY}))
        )

    if status is ObligationStatus.BLOCKED:
        return (
            bool(capabilities & WRITE_CAPABILITIES)
            and result_is_failed(result)
            and not project_artifact_evidence(result).persisted_mutation
        )

    return False


def _is_memory_write_observation(
    tool: str,
    capabilities: frozenset[str],
    observation: Mapping[str, Any],
) -> bool:
    if _MEMORY_CAPABILITY not in capabilities:
        return False
    result = observation.get("result")
    if not isinstance(result, Mapping):
        return False
    args = observation.get("args")
    if str(tool).casefold() == "session_memory" and isinstance(args, Mapping):
        return str(args.get("action", "")).casefold() in {"set", "delete"}
    if result.get("effect") == "memory_write":
        return True
    return any(metadata.get("effect") == "memory_write" for metadata in artifact_metadata(result))


def observed_effect_kinds(authority: Any, observation: Mapping[str, Any]) -> tuple[str, ...]:
    """Return only durable effects proved by one trusted observation."""

    result = observation.get("result")
    if not isinstance(result, Mapping):
        return ()
    capabilities = tool_capabilities(authority, str(observation.get("tool") or ""))
    if capabilities is None or result_executed(result) is not True:
        return ()
    if not project_artifact_evidence(result).persisted_mutation:
        return ()
    effects: list[str] = []
    if capabilities & WRITE_CAPABILITIES:
        effects.append("write")
    if _is_memory_write_observation(str(observation.get("tool") or ""), capabilities, observation):
        effects.append("memory_write")
    return tuple(effects)


__all__ = ["effect_observation_proves_terminal", "observed_effect_kinds", "tool_capabilities"]

"""Trusted projection helpers for the logical session-memory resource."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent.capabilities import Capability, canonical_capabilities
from agent.resources.contracts import ResourceAccess, ResourceMode, ResourceProvenance
from agent.tools.contracts import ToolOriginKind

MEMORY_RESOURCE = "memory"
MEMORY_TOOL = "session_memory"


def memory_resource_is_eligible(owner: Any) -> bool:
    """Return whether a trusted runtime surface owns logical memory access."""

    metadata = getattr(owner, "metadata", None)
    if isinstance(metadata, Mapping) and metadata.get("memory_resource_eligible") is True:
        return True
    registry = getattr(owner, "tool_registry", None)
    descriptor_for = getattr(registry, "descriptor", None)
    if not callable(descriptor_for):
        return False
    try:
        descriptor = descriptor_for(MEMORY_TOOL)
    except (KeyError, LookupError):
        return False
    origin = getattr(descriptor, "origin_kind", ToolOriginKind.BUILTIN)
    if origin is not ToolOriginKind.BUILTIN and str(getattr(origin, "value", origin)) != ToolOriginKind.BUILTIN.value:
        return False
    try:
        return Capability.MEMORY in canonical_capabilities(getattr(descriptor, "capabilities", ()))
    except (TypeError, ValueError):
        return False


def add_trusted_memory_scopes(
    owner: Any,
    read_resources: tuple[Any, ...],
    write_resources: tuple[Any, ...],
    permissions: Any,
) -> tuple[tuple[Any, ...], tuple[Any, ...]]:
    """Add logical memory scopes only from trusted capability and tool facts."""

    try:
        capabilities = canonical_capabilities(permissions)
    except (TypeError, ValueError):
        return read_resources, write_resources
    if Capability.MEMORY not in capabilities or not memory_resource_is_eligible(owner):
        return read_resources, write_resources
    read = tuple(read_resources)
    write = tuple(write_resources)
    if not any(getattr(item, "name", None) == MEMORY_RESOURCE for item in read):
        read = (
            *read,
            ResourceAccess(
                MEMORY_RESOURCE,
                ResourceMode.READ,
                ResourceProvenance.TRUSTED_DERIVED,
            ),
        )
    if not any(getattr(item, "name", None) == MEMORY_RESOURCE for item in write):
        write = (
            *write,
            ResourceAccess(
                MEMORY_RESOURCE,
                ResourceMode.WRITE,
                ResourceProvenance.TRUSTED_DERIVED,
            ),
        )
    return read, write


__all__ = [
    "MEMORY_RESOURCE",
    "MEMORY_TOOL",
    "add_trusted_memory_scopes",
    "memory_resource_is_eligible",
]

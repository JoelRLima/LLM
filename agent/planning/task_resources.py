"""Trusted resource authority for executable task-graph nodes."""

from __future__ import annotations

from typing import Any

from agent.capabilities import WRITE_CAPABILITIES, capability_values
from agent.resources.contracts import (
    WORKSPACE_RESOURCE,
    ResourceAccess,
    ResourceMode,
    ResourceProvenance,
    resources_conflict,
    resources_overlap,
)
from agent.tools.invocation_semantics import resolve_invocation_semantics

# Compatibility alias.  ResourceAccess is the canonical shape.
ResourceClaim = ResourceAccess


def normalize_resource_name(value: Any) -> str:
    """Return the historical lexical display form for old planning callers.

    ``ResourceAccess`` is the authority-bearing representation and applies
    the conservative workspace sentinel to traversal-like IDs.  This adapter
    preserves the old string helper's display behavior for compatibility, but
    every value crossing a claim/scheduling boundary is immediately wrapped
    in ``ResourceAccess`` and normalized canonically there.
    """

    if not isinstance(value, str) or not value.strip():
        return WORKSPACE_RESOURCE
    token = value.replace("\\", "/").strip()
    if token.casefold() in {"*", ".", "./", "workspace", "workspace-wide"}:
        return WORKSPACE_RESOURCE
    # Presentation-only compatibility projection.  It must never be used as
    # physical scope without ResourceAccess wrapping.
    import posixpath

    return posixpath.normpath(token).strip("/") or WORKSPACE_RESOURCE


def _mode(value: Any) -> str:
    raw = getattr(value, "value", value)
    return str(raw).casefold()


def declared_resource_claims(node: Any) -> tuple[ResourceClaim, ...]:
    return tuple(
        ResourceClaim(
            resource.name,
            _mode(resource.mode),
            ResourceProvenance.MODEL_DECLARED,
        )
        for resource in getattr(node, "resources", ())
    )


def node_is_mutating(node: Any) -> bool:
    metadata = getattr(node, "metadata", {})
    tool_name = str(metadata.get("tool") or ("code_task" if "action" in metadata else "")).casefold()
    if tool_name:
        class _Descriptor:
            name = tool_name
            capabilities = capability_values(getattr(node, "capabilities", ()))
            cacheable = False
            idempotent = False
            cancellation_safety = "unsupported"
        semantics = resolve_invocation_semantics(_Descriptor(), metadata)
        return semantics.may_mutate
    capabilities = capability_values(getattr(node, "capabilities", ()))
    # External/process/validation capabilities do not imply a workspace
    # mutation.  Preserve the conservative wildcard only for explicit write
    # authority when no operation name is available.
    return bool(capabilities & {item.value for item in WRITE_CAPABILITIES})


def trusted_resource_claims(node: Any) -> tuple[ResourceClaim, ...]:
    """Derive resources from the concrete operation, never from its claims."""

    metadata = getattr(node, "metadata", {})
    tool_name = str(metadata.get("tool") or ("code_task" if "action" in metadata else "")).casefold()
    if not tool_name:
        return () if not node_is_mutating(node) else (
            ResourceClaim(
                WORKSPACE_RESOURCE,
                ResourceMode.WRITE,
                ResourceProvenance.TRUSTED_DERIVED,
            ),
        )
    class _Descriptor:
        name = tool_name
        capabilities = capability_values(getattr(node, "capabilities", ()))
        cacheable = False
        idempotent = False
        cancellation_safety = "unsupported"
    semantics = resolve_invocation_semantics(_Descriptor(), metadata)
    if semantics.read_only:
        return tuple(
            ResourceClaim(access.name, access.mode, ResourceProvenance.TRUSTED_DERIVED)
            for access in semantics.resource_access
        )
    # A generated ChangeSet can discover collateral paths at execution time;
    # requested target claims are therefore not a physical scheduling fence.
    return (
        ResourceClaim(
            WORKSPACE_RESOURCE,
            ResourceMode.WRITE,
            ResourceProvenance.TRUSTED_DERIVED,
        ),
    )


def effective_resource_claims(node: Any) -> tuple[ResourceClaim, ...]:
    trusted = trusted_resource_claims(node)
    return trusted or declared_resource_claims(node)


def claims_overlap(left: str, right: str) -> bool:
    return resources_overlap(left, right)


def claims_conflict(left: ResourceClaim, right: ResourceClaim) -> bool:
    return resources_conflict(left, right)


def resource_claims_conflict(
    left: tuple[ResourceClaim, ...], right: tuple[ResourceClaim, ...]
) -> bool:
    return any(claims_conflict(first, second) for first in left for second in right)


__all__ = [
    "ResourceClaim",
    "WORKSPACE_RESOURCE",
    "claims_conflict",
    "claims_overlap",
    "declared_resource_claims",
    "effective_resource_claims",
    "node_is_mutating",
    "normalize_resource_name",
    "resource_claims_conflict",
    "trusted_resource_claims",
]

"""Trusted resource authority for executable task-graph nodes."""

from __future__ import annotations

import posixpath
from dataclasses import dataclass
from typing import Any

WORKSPACE_RESOURCE = "*"
_MUTATING_CAPABILITIES = frozenset(
    {
        "write",
        "vcs_write",
        "process",
        "network",
        "package_install",
        "memory",
        "validate",
    }
)
_CODE_READ_ACTIONS = frozenset({"analyze", "review"})
_CODE_WRITE_ACTIONS = frozenset({"generate", "modify", "repair", "refactor"})


@dataclass(frozen=True)
class ResourceClaim:
    name: str
    mode: str


def normalize_resource_name(value: Any) -> str:
    """Normalize one workspace-relative resource identity conservatively."""

    if not isinstance(value, str) or not value.strip():
        return WORKSPACE_RESOURCE
    token = value.replace("\\", "/").strip()
    if token in {"*", ".", "./", "workspace", "workspace-wide"}:
        return WORKSPACE_RESOURCE
    normalized = posixpath.normpath(token).replace("\\", "/")
    if normalized == "." or normalized == ".." or normalized.startswith("../"):
        return WORKSPACE_RESOURCE
    return normalized.strip("/") or WORKSPACE_RESOURCE


def _mode(value: Any) -> str:
    raw = getattr(value, "value", value)
    return str(raw).casefold()


def declared_resource_claims(node: Any) -> tuple[ResourceClaim, ...]:
    return tuple(
        ResourceClaim(normalize_resource_name(resource.name), _mode(resource.mode))
        for resource in getattr(node, "resources", ())
    )


def node_is_mutating(node: Any) -> bool:
    action = str(getattr(node, "metadata", {}).get("action", "")).casefold()
    capabilities = frozenset(str(item).casefold() for item in getattr(node, "capabilities", ()))
    return action in _CODE_WRITE_ACTIONS or bool(capabilities & _MUTATING_CAPABILITIES)


def _target_claims(node: Any, *, mode: str) -> tuple[ResourceClaim, ...]:
    raw_targets = getattr(node, "metadata", {}).get("targets", ())
    targets = tuple(
        normalize_resource_name(item)
        for item in raw_targets
        if isinstance(item, str) and item.strip()
    ) if isinstance(raw_targets, (list, tuple)) else ()
    if not targets:
        return (ResourceClaim(WORKSPACE_RESOURCE, mode),)
    return tuple(dict.fromkeys(ResourceClaim(target, mode) for target in targets))


def trusted_resource_claims(node: Any) -> tuple[ResourceClaim, ...]:
    """Derive resources from the concrete operation, never from its claims."""

    action = str(getattr(node, "metadata", {}).get("action", "")).casefold()
    if action in _CODE_READ_ACTIONS:
        return _target_claims(node, mode="read")
    if action in _CODE_WRITE_ACTIONS:
        claims = list(_target_claims(node, mode="write"))
        if bool(getattr(node, "metadata", {}).get("include_tests", False)):
            claims.append(ResourceClaim(WORKSPACE_RESOURCE, "write"))
        return tuple(dict.fromkeys(claims))
    if node_is_mutating(node):
        # A generic mutating node has no trusted footprint contract.  It must
        # serialize against every workspace write rather than trust omission.
        return (ResourceClaim(WORKSPACE_RESOURCE, "write"),)
    return ()


def effective_resource_claims(node: Any) -> tuple[ResourceClaim, ...]:
    trusted = trusted_resource_claims(node)
    return trusted or declared_resource_claims(node)


def claims_overlap(left: str, right: str) -> bool:
    left_name = normalize_resource_name(left)
    right_name = normalize_resource_name(right)
    if WORKSPACE_RESOURCE in {left_name, right_name} or left_name == right_name:
        return True
    left_parts = tuple(part for part in left_name.split("/") if part)
    right_parts = tuple(part for part in right_name.split("/") if part)
    return (
        len(left_parts) < len(right_parts) and right_parts[: len(left_parts)] == left_parts
    ) or (
        len(right_parts) < len(left_parts) and left_parts[: len(right_parts)] == right_parts
    )


def claims_conflict(left: ResourceClaim, right: ResourceClaim) -> bool:
    return claims_overlap(left.name, right.name) and "write" in {left.mode, right.mode}


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

"""Canonical capability vocabulary shared by planning and execution.

Capability names are serialized as strings at compatibility boundaries, but
the runtime keeps one closed vocabulary and derives its policy subsets from
it.  This module intentionally contains vocabulary only; authorization
policies remain owned by their respective gateways/modes.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Iterable


class Capability(str, Enum):
    READ = "read"
    WRITE = "write"
    PROCESS = "process"
    NETWORK = "network"
    MEMORY = "memory"
    ANALYZE = "analyze"
    VCS_READ = "vcs_read"
    VCS_WRITE = "vcs_write"
    PACKAGE_INSTALL = "package_install"
    VALIDATE = "validate"


ALL_CAPABILITIES = frozenset(Capability)
WRITE_CAPABILITIES = frozenset({Capability.WRITE, Capability.VCS_WRITE})
MUTATING_CAPABILITIES = frozenset(
    {
        Capability.WRITE,
        Capability.VCS_WRITE,
        Capability.PROCESS,
        Capability.NETWORK,
        Capability.PACKAGE_INSTALL,
        Capability.MEMORY,
        Capability.VALIDATE,
    }
)


def capability(value: Any) -> Capability:
    """Normalize one trusted or serialized capability, failing closed."""

    if isinstance(value, Capability):
        return value
    if not isinstance(value, str):
        raise ValueError("capability must be a string")
    try:
        return Capability(value.strip().casefold())
    except ValueError as exc:
        raise ValueError(f"unknown capability: {value!r}") from exc


def canonical_capabilities(values: Iterable[Any] | None) -> frozenset[Capability]:
    if values is None:
        return frozenset()
    if isinstance(values, (str, bytes, bytearray)):
        raise TypeError("capabilities must be a collection")
    return frozenset(capability(value) for value in values)


def capability_values(values: Iterable[Any] | None) -> frozenset[str]:
    """Return the explicit string form used by adapters and checkpoints."""

    return frozenset(item.value for item in canonical_capabilities(values))


def capability_subset(values: Iterable[Any] | None, subset: Iterable[Any]) -> frozenset[str]:
    known = canonical_capabilities(values)
    selected = canonical_capabilities(subset)
    return frozenset(item.value for item in known & selected)


__all__ = [
    "ALL_CAPABILITIES",
    "Capability",
    "MUTATING_CAPABILITIES",
    "WRITE_CAPABILITIES",
    "canonical_capabilities",
    "capability",
    "capability_subset",
    "capability_values",
]

"""Canonical logical resource identities and access claims.

These values describe logical task scope.  They deliberately do not resolve
filesystem paths or assert that a resource exists; confinement and persistent
artifact safety are separate policies.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

WORKSPACE_RESOURCE = "*"
_WINDOWS_ABSOLUTE = re.compile(r"^[A-Za-z]:")


class ResourceMode(str, Enum):
    READ = "read"
    WRITE = "write"


class ResourceProvenance(str, Enum):
    """Origin classification for a logical resource claim.

    A declaration supplied by a model is useful for planning context, but it
    is not the same fact as a resource derived from a registered operation or
    observed in a mutation artifact.  Keeping the origin on the shared shape
    prevents those claims from being silently interchangeable.
    """

    UNKNOWN = "unknown"
    MODEL_DECLARED = "model_declared"
    TRUSTED_DERIVED = "trusted_derived"
    OBSERVED_MUTATION = "observed_mutation"


def normalize_resource_id(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        return WORKSPACE_RESOURCE
    token = value.replace("\\", "/").strip()
    if token.casefold() in {"*", ".", "./", "workspace", "workspace-wide"}:
        return WORKSPACE_RESOURCE
    # Resource IDs are logical workspace-relative names.  An absolute path,
    # UNC path, or drive-qualified path cannot be safely compared with a
    # workspace-relative claim, so collapse it to the conservative sentinel.
    if token.startswith("/") or token.startswith("//") or _WINDOWS_ABSOLUTE.match(token):
        return WORKSPACE_RESOURCE
    # Do not turn a traversal expression into a different apparently-safe
    # logical name (for example ``src/../secrets.txt``).  Logical
    # normalization is not filesystem confinement, so ambiguous traversal is
    # deliberately widened to the conservative workspace scope.
    if ".." in token.split("/"):
        return WORKSPACE_RESOURCE
    normalized = posixpath.normpath(token).replace("\\", "/")
    if normalized in {".", ".."} or normalized.startswith("../"):
        return WORKSPACE_RESOURCE
    return normalized.strip("/") or WORKSPACE_RESOURCE


@dataclass(frozen=True, slots=True)
class ResourceAccess:
    """A normalized logical resource claim with explicit access mode."""

    name: str
    mode: ResourceMode | str = ResourceMode.READ
    provenance: ResourceProvenance | str = ResourceProvenance.UNKNOWN

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", normalize_resource_id(self.name))
        raw_mode = self.mode.value if isinstance(self.mode, ResourceMode) else str(self.mode).casefold()
        try:
            object.__setattr__(self, "mode", ResourceMode(raw_mode))
        except ValueError as exc:
            raise ValueError(f"unsupported resource mode: {self.mode!r}") from exc
        raw_provenance = (
            self.provenance.value
            if isinstance(self.provenance, ResourceProvenance)
            else str(self.provenance).strip().casefold()
        )
        try:
            object.__setattr__(self, "provenance", ResourceProvenance(raw_provenance))
        except ValueError as exc:
            raise ValueError(f"unsupported resource provenance: {self.provenance!r}") from exc

    @property
    def resource(self) -> str:
        return self.name

    @property
    def write(self) -> bool:
        return self.mode is ResourceMode.WRITE

    @property
    def origin(self) -> ResourceProvenance:
        if isinstance(self.provenance, ResourceProvenance):
            return self.provenance
        return ResourceProvenance(str(self.provenance))

    @property
    def trusted(self) -> bool:
        return self.provenance in {
            ResourceProvenance.TRUSTED_DERIVED,
            ResourceProvenance.OBSERVED_MUTATION,
        }


def resources_overlap(left: str, right: str) -> bool:
    left_name = normalize_resource_id(left)
    right_name = normalize_resource_id(right)
    if WORKSPACE_RESOURCE in {left_name, right_name} or left_name == right_name:
        return True
    left_parts = tuple(part for part in left_name.split("/") if part)
    right_parts = tuple(part for part in right_name.split("/") if part)
    return (
        len(left_parts) < len(right_parts) and right_parts[: len(left_parts)] == left_parts
    ) or (
        len(right_parts) < len(left_parts) and left_parts[: len(right_parts)] == right_parts
    )


def resources_conflict(left: ResourceAccess, right: ResourceAccess) -> bool:
    return resources_overlap(left.name, right.name) and (
        left.mode is ResourceMode.WRITE or right.mode is ResourceMode.WRITE
    )


__all__ = [
    "ResourceAccess",
    "ResourceMode",
    "ResourceProvenance",
    "WORKSPACE_RESOURCE",
    "normalize_resource_id",
    "resources_conflict",
    "resources_overlap",
]

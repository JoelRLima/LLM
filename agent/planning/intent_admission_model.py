"""Trusted models used by deterministic semantic-intent admission."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from agent.capabilities import (
    WRITE_CAPABILITIES,
    Capability,
    canonical_capabilities,
    capability_values,
)
from agent.resources.contracts import (
    ResourceAccess,
    ResourceMode,
    ResourceProvenance,
    normalize_resource_id,
    resource_is_within,
)

from .memory_authority import MEMORY_RESOURCE, add_trusted_memory_scopes


class IntentAdmissionError(ValueError):
    """Closed deterministic admission failure."""

    def __init__(self, reason_code: str, detail: str | None = None) -> None:
        self.reason_code = reason_code
        self.code = reason_code
        super().__init__(reason_code if detail is None else f"{reason_code}: {detail}")


_CANONICAL_DURABLE_EFFECTS = frozenset({"write", "memory_write"})
# This mapping is a semantic effect ceiling/claim projection only.  It is not
# the exact capability requirement for a selected invocation.  The concrete
# invocation semantics owner derives that requirement later.
_EFFECT_CAPABILITY = {
    "write": frozenset(WRITE_CAPABILITIES),
    "memory_write": frozenset({Capability.MEMORY}),
}
_OPERATION_CAPABILITIES = {
    "read": frozenset({Capability.READ}),
    "plan": frozenset({Capability.READ}),
    # DO is operational intent, not a filesystem request. Exact invocation
    # semantics derive the concrete capability later.
    "do": frozenset(),
}


def _resource_tuple(values: Any, *, mode: ResourceMode) -> tuple[ResourceAccess, ...]:
    if values is None:
        return ()
    if isinstance(values, (str, bytes, bytearray)):
        raise TypeError("resource scopes must be a collection")
    result: list[ResourceAccess] = []
    for value in values:
        if isinstance(value, ResourceAccess):
            access = value
        elif isinstance(value, str):
            access = ResourceAccess(value, mode, ResourceProvenance.TRUSTED_DERIVED)
        else:
            raise TypeError("resource scope contains an invalid value")
        if access.mode is not mode:
            raise IntentAdmissionError("AUTHORITY_RESOURCE_MODE_MISMATCH")
        if not access.trusted:
            raise IntentAdmissionError("AUTHORITY_RESOURCE_NOT_TRUSTED")
        result.append(access)
    return tuple(dict.fromkeys(result))


@dataclass(frozen=True, slots=True)
class AuthorityEnvelope:
    """Immutable trusted projection; never constructed from model output."""

    parent_permissions: frozenset[str] = frozenset()
    granted_effects: frozenset[str] = frozenset()
    read_resources: tuple[ResourceAccess, ...] = ()
    write_resources: tuple[ResourceAccess, ...] = ()
    approval_required: bool | None = None
    policy: Mapping[str, Any] = MappingProxyType({})
    workspace_root: str | None = None
    authority_identity: str = "runtime"

    def __post_init__(self) -> None:
        permissions = canonical_capabilities(self.parent_permissions)
        object.__setattr__(self, "parent_permissions", capability_values(permissions))
        effects = frozenset(
            value.strip().casefold()
            for value in self.granted_effects
            if isinstance(value, str) and value.strip()
        )
        if not effects.issubset(_CANONICAL_DURABLE_EFFECTS):
            raise IntentAdmissionError("AUTHORITY_UNKNOWN_GRANTED_EFFECT")
        object.__setattr__(self, "granted_effects", effects)
        object.__setattr__(self, "read_resources", _resource_tuple(self.read_resources, mode=ResourceMode.READ))
        object.__setattr__(self, "write_resources", _resource_tuple(self.write_resources, mode=ResourceMode.WRITE))
        if self.approval_required is not None and type(self.approval_required) is not bool:
            raise TypeError("approval_required must be a boolean or null")
        if not isinstance(self.policy, Mapping):
            raise TypeError("policy must be a mapping")
        object.__setattr__(self, "policy", MappingProxyType(dict(self.policy)))
        if self.workspace_root is not None:
            object.__setattr__(self, "workspace_root", str(Path(self.workspace_root).resolve()))
        if not isinstance(self.authority_identity, str) or not self.authority_identity.strip():
            raise ValueError("authority_identity must be non-empty")

    @classmethod
    def from_context(
        cls,
        context: Any,
        *,
        read_resources: Any = None,
        write_resources: Any = None,
        granted_effects: Any = None,
        policy: Mapping[str, Any] | None = None,
        workspace_root: str | Path | None = None,
        authority_identity: str = "runtime-context",
    ) -> "AuthorityEnvelope":
        """Project only trusted context fields into an envelope."""

        permissions = canonical_capabilities(getattr(context, "permissions", ()))
        metadata = getattr(context, "metadata", {})
        if not isinstance(metadata, Mapping):
            metadata = {}
        selected_read = read_resources if read_resources is not None else metadata.get("read_resources")
        selected_write = write_resources if write_resources is not None else metadata.get("write_resources")
        selected_workspace_root = workspace_root or metadata.get("workspace_root")
        trusted_read = tuple(selected_read or ())
        trusted_write = tuple(selected_write or ())
        trusted_read, trusted_write = add_trusted_memory_scopes(
            context,
            trusted_read,
            trusted_write,
            permissions,
        )
        selected_effects = (
            granted_effects
            if granted_effects is not None
            else {
                effect
                for effect in _EFFECT_CAPABILITY
                if (
                    bool(WRITE_CAPABILITIES & permissions)
                    if effect == "write"
                    else Capability.MEMORY in permissions
                    and any(item == MEMORY_RESOURCE or getattr(item, "name", None) == MEMORY_RESOURCE for item in trusted_write)
                )
            }
        )
        approval_value = getattr(context, "approval_required", None)
        return cls(
            parent_permissions=capability_values(permissions),
            granted_effects=frozenset(selected_effects),
            read_resources=trusted_read,
            write_resources=trusted_write,
            # TaskRuntimePolicy does not own approval policy.  Keep this fact
            # unknown unless a trusted caller explicitly supplied it.
            approval_required=approval_value if type(approval_value) is bool else None,
            policy=policy or {},
            workspace_root=(
                str(selected_workspace_root)
                if selected_workspace_root is not None
                else None
            ),
            authority_identity=authority_identity,
        )

    @property
    def capabilities(self) -> frozenset[str]:
        return self.parent_permissions

    def has_capability(self, capability: Capability | str) -> bool:
        selected = canonical_capabilities((capability,))
        return all(item.value in self.parent_permissions for item in selected)

    def allows_read(self, resource: str) -> bool:
        return _scope_allows(self.read_resources, resource)

    def allows_write(self, resource: str) -> bool:
        return _scope_allows(self.write_resources, resource)


def _scope_allows(scope: tuple[ResourceAccess, ...], resource: str) -> bool:
    normalized = normalize_resource_id(resource)
    if normalized == MEMORY_RESOURCE:
        return any(item.name == MEMORY_RESOURCE for item in scope)
    if any(item.name == MEMORY_RESOURCE for item in scope):
        # A logical memory grant is never a filesystem grant.
        scope = tuple(item for item in scope if item.name != MEMORY_RESOURCE)
    return any(resource_is_within(item.name, normalized) for item in scope)


@dataclass(frozen=True, slots=True)
class AdmittedEffect:
    effect: str
    selector_ids: tuple[str, ...]
    prohibited: bool = False


@dataclass(frozen=True, slots=True)
class AdmittedSelector:
    selector_id: str
    kind: str
    value: str
    role: str
    literal_resource: str | None = None
    symbolic: bool = False


@dataclass(frozen=True, slots=True)
class AdmittedIntent:
    """Trusted-derived semantic projection for downstream consumers."""

    operation: str
    capabilities: frozenset[str]
    requested_effects: tuple[AdmittedEffect, ...]
    prohibited_effects: tuple[AdmittedEffect, ...]
    selectors: tuple[AdmittedSelector, ...]
    constraints: tuple[Any, ...]
    evidence_spans: tuple[Any, ...]
    proposal_only: bool
    approval_required: bool | None
    requires_grounding: bool = False
    requires_validation: bool = False
    admitted_literal_targets: tuple[str, ...] = ()
    authority_identity: str = "runtime"
    claim_fingerprint: str = ""

    @property
    def admitted_effects(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(item.effect for item in self.requested_effects))

    @property
    def grounded_targets(self) -> tuple[str, ...]:
        return self.admitted_literal_targets

    @property
    def mutation_selector_ids(self) -> tuple[str, ...]:
        """Selectors explicitly bound to a requested durable mutation."""

        return tuple(
            dict.fromkeys(
                selector_id
                for item in self.requested_effects
                if item.effect in _CANONICAL_DURABLE_EFFECTS
                for selector_id in item.selector_ids
            )
        )

    @property
    def mutation_targets(self) -> tuple[str, ...]:
        return self.admitted_literal_targets

    @property
    def can_mutate(self) -> bool:
        return not self.proposal_only and not self.requires_grounding and bool(self.requested_effects)

    def contains_target(self, resource: str) -> bool:
        return any(
            resource_is_within(target, resource)
            for target in self.admitted_literal_targets
        )

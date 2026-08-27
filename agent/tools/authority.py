"""Immutable authority snapshots captured at application/task boundaries."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Mapping, cast
from uuid import uuid4

from agent.capabilities import Capability, capability_values
from agent.tools.extension_state import validate_extension_id
from agent.tools.runtime_identity import RuntimeSnapshotIdentity
from agent.tools.workspace_extensions_resolver import ResolvedWorkspaceExtensions


class OperationalMode(str, Enum):
    """User-facing capability ceiling projected onto canonical authority."""

    READ_ONLY = "read-only"
    EDITOR = "editor"
    FULL = "full"

    @property
    def display_name(self) -> str:
        return {
            OperationalMode.READ_ONLY: "READ ONLY",
            OperationalMode.EDITOR: "EDITOR",
            OperationalMode.FULL: "FULL",
        }[self]

    @classmethod
    def parse(cls, value: str) -> "OperationalMode | None":
        normalized = value.strip().casefold().replace("_", "-")
        if normalized in {"read", "readonly", "read-only"}:
            return cls.READ_ONLY
        if normalized == "editor":
            return cls.EDITOR
        if normalized == "full":
            return cls.FULL
        return None


def operational_mode_capabilities(mode: OperationalMode) -> frozenset[str] | None:
    """Return the mode ceiling; ``None`` means no additional reduction."""

    if mode is OperationalMode.FULL:
        return None
    capabilities = {
        item.value
        for item in (Capability.READ, Capability.VCS_READ, Capability.ANALYZE)
    }
    if mode is OperationalMode.EDITOR:
        capabilities.update(capability_values((Capability.WRITE, Capability.VALIDATE)))
    return frozenset(capabilities)


def _capabilities(values: Iterable[str]) -> frozenset[str]:
    if isinstance(values, str):
        raise TypeError("capabilities deve ser uma coleção de strings")
    result = frozenset(values)
    if any(type(value) is not str or not value.strip() for value in result):
        raise ValueError("capabilities contém valor inválido")
    return result


@dataclass(frozen=True, slots=True, init=False)
class ApplicationAuthoritySnapshot:
    """Trusted authority captured from one bootstrap snapshot."""

    runtime_identity: RuntimeSnapshotIdentity
    _extension_grants: tuple[tuple[str, frozenset[str]], ...]
    policy_version: str | None
    provenance: str

    def __init__(
        self,
        runtime_identity: RuntimeSnapshotIdentity | None = None,
        *,
        workspace_id: str | None = None,
        snapshot_id: str | None = None,
        extension_grants: Mapping[str, frozenset[str]] | None = None,
        policy_version: str | None = None,
        provenance: str = "application_bootstrap",
    ) -> None:
        if runtime_identity is None:
            if workspace_id is None:
                raise ValueError("runtime_identity ou workspace_id é obrigatório")
            runtime_identity = RuntimeSnapshotIdentity(
                snapshot_id=snapshot_id or str(uuid4()),
                workspace_id=workspace_id,
            )
        elif workspace_id is not None and runtime_identity.workspace_id != workspace_id:
            raise ValueError("workspace_id diverge da runtime_identity")
        if not isinstance(runtime_identity, RuntimeSnapshotIdentity):
            raise TypeError("runtime_identity inválida")
        values = extension_grants or {}
        if not isinstance(values, Mapping):
            raise TypeError("extension_grants deve ser um mapping")
        normalized: list[tuple[str, frozenset[str]]] = []
        for extension_id, grants in values.items():
            validate_extension_id(extension_id)
            normalized.append((extension_id, _capabilities(grants)))
        if policy_version is not None and not isinstance(policy_version, str):
            raise TypeError("policy_version inválida")
        if not isinstance(provenance, str) or not provenance.strip():
            raise ValueError("provenance inválida")
        object.__setattr__(self, "runtime_identity", runtime_identity)
        object.__setattr__(self, "_extension_grants", tuple(sorted(normalized)))
        object.__setattr__(self, "policy_version", policy_version)
        object.__setattr__(self, "provenance", provenance)

    @property
    def snapshot_id(self) -> str:
        return str(cast(RuntimeSnapshotIdentity, self.runtime_identity).snapshot_id)

    @property
    def workspace_id(self) -> str:
        return str(cast(RuntimeSnapshotIdentity, self.runtime_identity).workspace_id)

    @property
    def extension_grants(self) -> dict[str, frozenset[str]]:
        return {key: frozenset(value) for key, value in self._extension_grants}

    def has_extension_grants(self, extension_id: str) -> bool:
        return any(key == extension_id for key, _ in self._extension_grants)

    def grants_for(self, extension_id: str) -> frozenset[str] | None:
        return next((value for key, value in self._extension_grants if key == extension_id), None)

    @classmethod
    def from_resolved(
        cls,
        workspace_id: str,
        resolved: ResolvedWorkspaceExtensions,
        *,
        runtime_identity: RuntimeSnapshotIdentity | None = None,
        provenance: str = "application_bootstrap",
    ) -> "ApplicationAuthoritySnapshot":
        grants = {
            entry.extension_id: frozenset(entry.configured_grants)
            for entry in resolved.entries
        }
        return cls(
            runtime_identity=runtime_identity,
            workspace_id=workspace_id,
            extension_grants=grants,
            provenance=provenance,
        )


@dataclass(frozen=True, slots=True)
class TaskAuthoritySnapshot:
    """Explicit trusted task authority; absence is represented by ``None``."""

    allowed_capabilities: frozenset[str] = field(default_factory=frozenset)
    policy_source: str | None = None
    snapshot_id: str = field(default_factory=lambda: str(uuid4()))
    runtime_identity: RuntimeSnapshotIdentity | None = field(default=None, kw_only=True)

    def __post_init__(self) -> None:
        object.__setattr__(self, "allowed_capabilities", _capabilities(self.allowed_capabilities))
        if not isinstance(self.snapshot_id, str) or not self.snapshot_id.strip():
            raise ValueError("snapshot_id deve ser uma string não vazia")
        if self.policy_source is not None and not isinstance(self.policy_source, str):
            raise TypeError("policy_source inválido")


        if self.runtime_identity is not None and not isinstance(
            self.runtime_identity, RuntimeSnapshotIdentity
        ):
            raise TypeError("runtime_identity inválida")


def bind_task_authority(
    capabilities: Iterable[str],
    application_authority: ApplicationAuthoritySnapshot | None,
    *,
    policy_source: str,
) -> TaskAuthoritySnapshot:
    """Bind explicit product input to the current trusted runtime snapshot.

    The caller supplies the task capabilities outside the model.  Binding the
    resulting snapshot to the bootstrap identity prevents it from being
    replayed against another workspace or application snapshot.
    """

    if not isinstance(application_authority, ApplicationAuthoritySnapshot):
        raise TypeError("application_authority invalida")
    return TaskAuthoritySnapshot(
        allowed_capabilities=_capabilities(capabilities),
        policy_source=policy_source,
        runtime_identity=application_authority.runtime_identity,
    )


@dataclass(frozen=True, slots=True)
class EffectiveTaskAuthority:
    """Task capabilities restricted by an optional canonical persona policy."""

    allowed_capabilities: frozenset[str]
    task_snapshot_id: str
    persona_policy_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "allowed_capabilities", _capabilities(self.allowed_capabilities))


def derive_effective_task_authority(
    task_authority: TaskAuthoritySnapshot | None,
    persona_restrictions: Iterable[str] | None,
) -> EffectiveTaskAuthority | None:
    """Intersect explicit task authority with restrictive persona capabilities."""

    if task_authority is None:
        return None
    persona = frozenset() if persona_restrictions is None else _capabilities(persona_restrictions)
    return EffectiveTaskAuthority(
        allowed_capabilities=task_authority.allowed_capabilities & persona,
        task_snapshot_id=task_authority.snapshot_id,
        persona_policy_id="persona" if persona_restrictions is not None else None,
    )


__all__ = [
    "ApplicationAuthoritySnapshot",
    "EffectiveTaskAuthority",
    "OperationalMode",
    "TaskAuthoritySnapshot",
    "bind_task_authority",
    "derive_effective_task_authority",
    "operational_mode_capabilities",
]

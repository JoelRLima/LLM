"""Immutable target-grounding records and closed grounding failures."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


class GroundingError(ValueError):
    """Closed deterministic grounding failure."""

    def __init__(self, reason_code: str, detail: str | None = None) -> None:
        self.reason_code = reason_code
        self.code = reason_code
        super().__init__(reason_code if detail is None else f"{reason_code}: {detail}")


@dataclass(frozen=True, slots=True)
class SourceInventoryEntry:
    """One source file admitted by the current read-scope inventory."""

    resource: str
    path: Path
    size: int


@dataclass(frozen=True, slots=True)
class SymbolDefinition:
    """One structurally classified definition from the canonical inventory."""

    resource: str
    path: Path
    source_sha256: str
    locations: tuple[tuple[int, int, int, int], ...]


@dataclass(frozen=True, slots=True)
class GroundingCandidate:
    selector_id: str
    symbol: str
    resource: str
    definition_line: int
    definition_column: int
    definition_end_line: int
    definition_end_column: int
    provenance: str
    source_sha256: str
    workspace_root_identity: str


@dataclass(frozen=True, slots=True)
class GroundedTarget:
    """Trusted-derived target plus the state needed for revalidation."""

    selector_id: str
    resource: str
    selector_kind: str
    symbol: str | None
    provenance: str
    discovery_scope: tuple[str, ...]
    definition_line: int | None
    definition_column: int | None
    source_sha256: str | None
    workspace_root_identity: str
    freshness_token: str
    mutation_authorized: bool

    @property
    def canonical_resource(self) -> str:
        return self.resource


@dataclass(frozen=True, slots=True)
class GroundedTargetSet:
    targets: tuple[GroundedTarget, ...]
    workspace_root_identity: str
    authority_identity: str
    mutation_selector_ids: tuple[str, ...] = ()
    max_files: int = 4096
    max_source_bytes: int = 2_000_000
    max_total_source_bytes: int = 64 * 1024 * 1024
    max_enumerated_paths: int = 16384
    max_candidates: int = 512

    @property
    def resources(self) -> tuple[str, ...]:
        return tuple(item.resource for item in self.targets)

    @property
    def mutation_targets(self) -> tuple[str, ...]:
        bound = set(self.mutation_selector_ids)
        return tuple(
            item.resource
            for item in self.targets
            if item.mutation_authorized and item.selector_id in bound
        )

    @property
    def selector_ids(self) -> tuple[str, ...]:
        return tuple(item.selector_id for item in self.targets)

    def for_selector(self, selector_id: str) -> GroundedTarget:
        for item in self.targets:
            if item.selector_id == selector_id:
                return item
        raise GroundingError("GROUNDING_SELECTOR_NOT_FOUND")

    def revalidate(
        self,
        workspace_root: str | Path,
        envelope: Any = None,
        *,
        admitted_intent: Any = None,
        required_capabilities: Any = (),
        required_effects: Any = (),
    ) -> "GroundedTargetSet":
        from .target_grounding import revalidate_grounded_targets

        return revalidate_grounded_targets(
            self,
            workspace_root,
            envelope=envelope,
            admitted_intent=admitted_intent,
            required_capabilities=required_capabilities,
            required_effects=required_effects,
        )

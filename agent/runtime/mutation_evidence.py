"""Canonical mutation footprint/evidence projection.

Runtime, planning, and reporting consumers use this immutable projection
instead of independently interpreting artifact metadata or tool names.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent.runtime.mutation_evidence_support import (
    _normalize_path,
    artifact_metadata,
    metadata_is_persisted_mutation,
    project_metadata_records,
)


@dataclass(frozen=True, slots=True)
class MutationEvidence:
    attempted: bool = False
    occurred: bool = False
    rollback_occurred: bool = False
    survives: bool = False
    affected_resources: tuple[str, ...] = ()
    mutated_resources: tuple[str, ...] = ()
    surviving_resources: tuple[str, ...] = ()
    validation_status: str | None = None
    final_state: str | None = None

    # Compatibility projections used by existing receipt/checkpoint schemas.
    @property
    def affected_files(self) -> tuple[str, ...]:
        return self.affected_resources

    @property
    def mutated_files(self) -> tuple[str, ...]:
        return self.mutated_resources

    @property
    def surviving_files(self) -> tuple[str, ...]:
        return self.surviving_resources

    @property
    def mutation_occurred(self) -> bool:
        return self.occurred

    @property
    def persisted_mutation(self) -> bool:
        return self.survives


def project_mutation_evidence(result: Any) -> MutationEvidence:
    affected: list[str] = []
    mutated: list[str] = []
    surviving: list[str] = []
    attempted = occurred = rollback = survives = explicit_unknown = False
    rollback_conflict = False
    validation: str | None = None
    for projection in project_metadata_records(result):
        attempted = attempted or projection.attempted
        occurred = occurred or projection.occurred
        rollback = rollback or projection.rollback
        survives = survives or projection.survives
        explicit_unknown = explicit_unknown or projection.explicit_unknown
        rollback_conflict = rollback_conflict or (projection.rollback and projection.survives)
        validation = projection.validation or validation
        _append_paths(affected, projection.affected_paths)
        _append_paths(mutated, projection.mutated_paths)
        _append_paths(surviving, projection.surviving_paths)
    final_state = _final_state(
        rollback_conflict, rollback, survives, occurred, explicit_unknown,
    )
    return MutationEvidence(
        attempted=attempted,
        occurred=occurred,
        rollback_occurred=rollback,
        survives=survives,
        affected_resources=tuple(affected),
        mutated_resources=tuple(mutated),
        surviving_resources=tuple(surviving),
        validation_status=validation,
        final_state=final_state,
    )


def _append_paths(target: list[str], paths: tuple[str, ...]) -> None:
    for raw_path in paths:
        path = _normalize_path(raw_path)
        if path and path != "." and path not in target:
            target.append(path)


def _final_state(
    rollback_conflict: bool,
    rollback: bool,
    survives: bool,
    occurred: bool,
    explicit_unknown: bool,
) -> str | None:
    if rollback_conflict or (rollback and survives):
        return "unknown"
    if rollback:
        return "restored"
    if survives:
        return "applied"
    if occurred or explicit_unknown:
        return "unknown"
    return None


__all__ = [
    "MutationEvidence",
    "artifact_metadata",
    "metadata_is_persisted_mutation",
    "project_mutation_evidence",
]

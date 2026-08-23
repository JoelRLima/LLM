"""Lossless bounded mutation-footprint projection."""

from __future__ import annotations

import posixpath
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


def _normalize_effect_path(path: Any) -> str:
    return posixpath.normpath(str(path).replace("\\", "/"))


def metadata_is_persisted_mutation(metadata: Mapping[str, Any]) -> bool:
    """Whether an artifact proves a mutation persisted in the final state."""

    return (
        metadata.get("persisted_mutation") is True
        and metadata.get("rollback_occurred") is not True
    ) or (
        metadata.get("applied") is True
        and metadata.get("mutation_occurred") is True
        and metadata.get("rollback_occurred") is not True
        and metadata.get("final_state") == "applied"
    )


@dataclass(frozen=True, slots=True)
class ArtifactEvidence:
    """Read-only aggregate of mutation, validation, rollback and file facts."""

    affected_files: tuple[str, ...] = ()
    mutated_files: tuple[str, ...] = ()
    surviving_files: tuple[str, ...] = ()
    validation_status: str | None = None
    mutation_occurred: bool = False
    rollback_occurred: bool = False
    persisted_mutation: bool = False


def project_artifact_evidence(result: Any) -> ArtifactEvidence:
    """Project artifact facts without allowing prose or tool status to add effects."""

    from agent.reporting.observation_evidence import artifact_metadata

    files: list[str] = []
    mutated_files: list[str] = []
    surviving_files: list[str] = []
    validation_status: str | None = None
    mutation_occurred = False
    rollback_occurred = False
    persisted_mutation = False
    for metadata in artifact_metadata(result):
        affected = metadata.get("affected_files")
        paths = affected if isinstance(affected, Sequence) and not isinstance(affected, (str, bytes, bytearray)) else ()
        for path in paths:
            value = _normalize_effect_path(path)
            if value not in files:
                files.append(value)
            if metadata.get("applied") is True and metadata.get("mutation_occurred") is True and value not in mutated_files:
                mutated_files.append(value)
            if metadata_is_persisted_mutation(metadata) and value not in surviving_files:
                surviving_files.append(value)
        mutation = metadata.get("applied") is True and metadata.get("mutation_occurred") is True
        if metadata.get("validation") is not None:
            validation_status = str(metadata["validation"])
        rollback_occurred = rollback_occurred or (
            metadata.get("rollback_occurred") is True or metadata.get("final_state") == "restored"
        )
        mutation_occurred = mutation_occurred or mutation
        persisted_mutation = persisted_mutation or metadata_is_persisted_mutation(metadata)
    return ArtifactEvidence(
        affected_files=tuple(files),
        mutated_files=tuple(mutated_files),
        surviving_files=tuple(surviving_files),
        validation_status=validation_status,
        mutation_occurred=mutation_occurred,
        rollback_occurred=rollback_occurred,
        persisted_mutation=persisted_mutation,
    )


__all__ = ["ArtifactEvidence", "metadata_is_persisted_mutation", "project_artifact_evidence"]

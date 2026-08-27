"""Compatibility projection for canonical runtime mutation evidence."""

from __future__ import annotations

from agent.runtime.mutation_evidence import (
    MutationEvidence,
    metadata_is_persisted_mutation,
    project_mutation_evidence,
)

ArtifactEvidence = MutationEvidence
project_artifact_evidence = project_mutation_evidence

__all__ = ["ArtifactEvidence", "metadata_is_persisted_mutation", "project_artifact_evidence"]

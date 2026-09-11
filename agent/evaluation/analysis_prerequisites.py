"""Strict validation of persisted release prerequisites for final analysis."""

from __future__ import annotations

from typing import Any, Mapping

from agent.evaluation.analysis_support import CampaignAnalysisError
from agent.evaluation.release_prerequisites import validate_release_prerequisite_projection


def validate_persisted_prerequisites(report: Mapping[str, Any]) -> None:
    """Reject a final report whose persisted prerequisites are incomplete."""

    projection = report.get("prerequisite_snapshots")
    errors = validate_release_prerequisite_projection(
        projection if isinstance(projection, Mapping) else None
    )
    if errors:
        raise CampaignAnalysisError(
            "persisted prerequisite projection is invalid: " + ", ".join(errors)
        )
    assert isinstance(projection, Mapping)
    persisted_installed = projection["installed_acceptance"]
    if report.get("installed_acceptance") != persisted_installed:
        raise CampaignAnalysisError("persisted installed acceptance is not self-consistent")
    persisted_readiness = projection["deterministic_readiness"]
    top_readiness = report.get("deterministic_readiness")
    if not isinstance(top_readiness, Mapping):
        raise CampaignAnalysisError("persisted deterministic readiness is missing")
    nested_readiness = persisted_readiness["deterministic_readiness"]
    if not isinstance(nested_readiness, Mapping):
        raise CampaignAnalysisError("persisted deterministic readiness summary is missing")
    for field in (
        "candidate_identity",
        "semantic_manifest_hash",
        "fixture_identity",
        "h_series_version",
        "repetition_policy",
        "complete",
        "reason_codes",
        "recorded",
    ):
        expected = (
            persisted_readiness.get(field)
            if field in persisted_readiness
            else nested_readiness.get(field)
        )
        if field in {"complete", "reason_codes", "recorded"}:
            expected = nested_readiness.get(field)
        if top_readiness.get(field) != expected:
            raise CampaignAnalysisError(
                f"persisted deterministic readiness field is inconsistent: {field}"
            )


__all__ = ["validate_persisted_prerequisites"]

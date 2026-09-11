"""Bounded, lossless projection of release-prerequisite evidence.

The ordinary evidence sanitizer is intentionally depth- and item-bounded.  A
release report therefore stores this small, schema-controlled projection rather
than the complete readiness artifact.  Every value below is selected by field
name and type; no arbitrary readiness subtree crosses the report boundary.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent.evaluation.scenario_contracts import RepetitionPolicy

RELEASE_PREREQUISITE_PROJECTION_SCHEMA = "RELEASE-PREREQUISITES-V1"
_MAX_COUNT_FIELDS = 32
_REQUIRED_SUMMARY_FIELDS = (
    "total",
    "passed",
    "failed",
    "unknown_failures",
    "valid_scenario_repetitions",
    "passed_scenario_repetitions",
    "environmental_attempts",
    "arm_executions",
    "h2_repetitions",
)
_REQUIRED_ANALYSIS_IDENTITY_FIELDS = (
    "consistent",
    "expected_candidate_identity",
    "expected_epoch",
    "expected_fixture_identity",
    "expected_model_config_fingerprint",
    "run_count_checked",
)
_REQUIRED_ANALYSIS_REPETITION_FIELDS = (
    "complete",
    "valid_scenario_repetitions",
    "passed_scenario_repetitions",
    "aggregate_pass_rate",
    "reason_codes",
)
_REQUIRED_ANALYSIS_FIELDS = (
    "analysis_schema_version",
    "evidence_envelope",
    "identity",
    "repetition",
    "valid_run_count",
    "environmental_attempt_count",
    "unknown_failed_run_count",
    "causal_failure_counts",
    "incidents",
    "policy",
    "release_verdict",
    "reason_codes",
)
_DEPTH_MARKERS = frozenset({"[DEPTH_LIMIT]", "[ITEM_LIMIT]"})
_CANDIDATE_FIELDS = (
    "head",
    "semantic_candidate_fingerprint",
    "semantic_manifest_hash",
    "source_fingerprint",
)
_INSTALLED_FIELDS = (
    "schema_version",
    "status",
    "acceptance",
    "mode",
    "clean",
    "evidence_level",
    "candidate",
    "candidate_identity",
    "semantic_manifest_hash",
    "wheel_sha256",
    "task_files_in_wheel",
)


def _candidate_projection(value: Any) -> dict[str, Any]:
    source = value if isinstance(value, Mapping) else {}
    return {field: source.get(field) for field in _CANDIDATE_FIELDS}


def _count_projection(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or len(value) > _MAX_COUNT_FIELDS:
        return {"__invalid_count_map__": None}
    return {str(key): value[key] for key in sorted(value, key=str)}


def _scalar_mapping_projection(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or len(value) > _MAX_COUNT_FIELDS:
        return {"__invalid_scalar_map__": None}
    return {str(key): value[key] for key in sorted(value, key=str)}


def _summary_projection(value: Any) -> dict[str, Any]:
    source = value if isinstance(value, Mapping) else {}
    return {field: source.get(field) for field in _REQUIRED_SUMMARY_FIELDS}


def _scenario_projection(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        entries = ((item.get("h_id"), item) for item in value if isinstance(item, Mapping))
    elif isinstance(value, Mapping):
        entries = (
            (h_id, value[h_id])
            for h_id in sorted(value, key=lambda item: (len(str(item)), str(item)))
        )
    else:
        return []
    projected: list[dict[str, Any]] = []
    for h_id, raw in entries:
        source = raw if isinstance(raw, Mapping) else {}
        projected.append(
            {
                "h_id": str(h_id),
                "arm_executions": source.get("arm_executions"),
                "failures": source.get("failures"),
                "pass_rate": source.get("pass_rate"),
                "passes": source.get("passes"),
                "valid_repetitions": source.get("valid_repetitions"),
                "policy_proof": source.get("policy_proof"),
                "scenario_passes": list(source.get("scenario_passes", ()))
                if isinstance(source.get("scenario_passes"), (list, tuple))
                else source.get("scenario_passes"),
                "scenario_repetition_numbers": list(source.get("scenario_repetition_numbers", ()))
                if isinstance(source.get("scenario_repetition_numbers"), (list, tuple))
                else source.get("scenario_repetition_numbers"),
            }
        )
    return projected


def _analysis_projection(value: Any) -> dict[str, Any]:
    source = value if isinstance(value, Mapping) else {}
    envelope = source.get("evidence_envelope")
    envelope_source = envelope if isinstance(envelope, Mapping) else {}
    identity = source.get("identity")
    identity_source = identity if isinstance(identity, Mapping) else {}
    repetition = source.get("repetition")
    repetition_source = repetition if isinstance(repetition, Mapping) else {}
    policy = source.get("policy")
    policy_source = policy if isinstance(policy, Mapping) else {}
    return {
        "analysis_schema_version": source.get("analysis_schema_version"),
        "evidence_envelope": {
            "valid": envelope_source.get("valid"),
            "run_count": envelope_source.get("run_count"),
            "errors": list(envelope_source.get("errors", ()))
            if isinstance(envelope_source.get("errors"), (list, tuple))
            else envelope_source.get("errors"),
        },
        "identity": {
            field: identity_source.get(field)
            for field in _REQUIRED_ANALYSIS_IDENTITY_FIELDS
        },
        "repetition": {
            field: repetition_source.get(field)
            for field in _REQUIRED_ANALYSIS_REPETITION_FIELDS
        }
        | {"per_scenario": _scenario_projection(repetition_source.get("per_scenario"))},
        "valid_run_count": source.get("valid_run_count"),
        "environmental_attempt_count": source.get("environmental_attempt_count"),
        "unknown_failed_run_count": source.get("unknown_failed_run_count"),
        "causal_failure_counts": _count_projection(source.get("causal_failure_counts")),
        "incidents": _count_projection(source.get("incidents")),
        "policy": _scalar_mapping_projection(policy_source),
        "release_verdict": source.get("release_verdict"),
        "reason_codes": list(source.get("reason_codes", ()))
        if isinstance(source.get("reason_codes"), (list, tuple))
        else source.get("reason_codes"),
    }


def _installed_projection(value: Any) -> dict[str, Any]:
    source = value if isinstance(value, Mapping) else {}
    return {
        "schema_version": source.get("schema_version"),
        "status": source.get("status"),
        "acceptance": source.get("acceptance"),
        "mode": source.get("mode"),
        "clean": source.get("clean", True),
        "evidence_level": source.get("evidence_level"),
        "candidate": _candidate_projection(source.get("candidate")),
        "candidate_identity": source.get("candidate_identity"),
        "semantic_manifest_hash": source.get("semantic_manifest_hash"),
        "wheel_sha256": source.get("wheel_sha256"),
        "task_files_in_wheel": source.get("task_files_in_wheel"),
    }


def _deterministic_projection(value: Any) -> dict[str, Any]:
    source = value if isinstance(value, Mapping) else {}
    dry_run = source.get("dry_run")
    dry_run_source = dry_run if isinstance(dry_run, Mapping) else {}
    summary = source.get("summary")
    if not isinstance(summary, Mapping):
        summary = dry_run_source.get("summary")
    analysis = source.get("analysis")
    if not isinstance(analysis, Mapping):
        analysis = dry_run_source.get("analysis")
    model = source.get("model_identity_schema")
    if not isinstance(model, Mapping):
        model = source.get("model_identity")
    model_source = model if isinstance(model, Mapping) else {}
    readiness = source.get("deterministic_readiness")
    readiness_source = readiness if isinstance(readiness, Mapping) else {}
    return {
        "schema_version": source.get("schema_version"),
        "ready": source.get("ready"),
        "reason_codes": list(source.get("reason_codes", ()))
        if isinstance(source.get("reason_codes"), (list, tuple))
        else source.get("reason_codes"),
        "campaign_started": source.get("campaign_started"),
        "candidate": _candidate_projection(source.get("candidate")),
        "candidate_identity": source.get("candidate_identity"),
        "semantic_manifest_hash": source.get("semantic_manifest_hash"),
        "fixture_identity": source.get("fixture_identity"),
        "h_series_version": source.get("h_series_version"),
        "epoch": source.get("epoch"),
        "repetition_policy": {
            field: (source.get("repetition_policy") or {}).get(field)
            if isinstance(source.get("repetition_policy"), Mapping)
            else None
            for field in RepetitionPolicy().__dict__
        },
        "model_identity": {
            field: model_source.get(field)
            for field in (
                "model_config_fingerprint",
                "profile",
                "provider",
                "model",
                "evidence_level",
            )
        },
        "deterministic_readiness": {
            "recorded": readiness_source.get("recorded"),
            "complete": readiness_source.get("complete"),
            "source": readiness_source.get("source"),
            "reason": readiness_source.get("reason"),
            "reason_codes": list(readiness_source.get("reason_codes", ()))
            if isinstance(readiness_source.get("reason_codes"), (list, tuple))
            else readiness_source.get("reason_codes"),
        },
        "summary": _summary_projection(summary),
        "analysis": _analysis_projection(analysis),
    }


def project_release_prerequisite_snapshot(
    installed_acceptance: Mapping[str, Any] | None,
    deterministic_readiness: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Return the complete bounded release-prerequisite projection."""

    return {
        "projection_schema_version": RELEASE_PREREQUISITE_PROJECTION_SCHEMA,
        "installed_acceptance": _installed_projection(installed_acceptance),
        "deterministic_readiness": _deterministic_projection(deterministic_readiness),
    }


def validate_release_prerequisite_projection(value: Mapping[str, Any] | None) -> tuple[str, ...]:
    from agent.evaluation.release_prerequisites_validation import (
        validate_release_prerequisite_projection as validate,
    )

    return validate(value)


def release_prerequisite_projection_is_valid(value: Mapping[str, Any] | None) -> bool:
    return not validate_release_prerequisite_projection(value)


__all__ = [
    "RELEASE_PREREQUISITE_PROJECTION_SCHEMA",
    "project_release_prerequisite_snapshot",
    "release_prerequisite_projection_is_valid",
    "validate_release_prerequisite_projection",
]

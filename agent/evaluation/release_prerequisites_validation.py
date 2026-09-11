"""Validation owner for the bounded release-prerequisite projection."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from agent.evaluation.release_prerequisites import (
    _CANDIDATE_FIELDS,
    _DEPTH_MARKERS,
    _INSTALLED_FIELDS,
    _MAX_COUNT_FIELDS,
    _REQUIRED_ANALYSIS_FIELDS,
    _REQUIRED_ANALYSIS_IDENTITY_FIELDS,
    _REQUIRED_ANALYSIS_REPETITION_FIELDS,
    _REQUIRED_SUMMARY_FIELDS,
    RELEASE_PREREQUISITE_PROJECTION_SCHEMA,
)
from agent.evaluation.scenario_contracts import H_SERIES, RepetitionPolicy

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _has_all(mapping: Mapping[str, Any], fields: Sequence[str]) -> bool:
    return all(field in mapping and mapping[field] is not None for field in fields)


def _valid_string_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _valid_candidate(value: Any) -> bool:
    return isinstance(value, Mapping) and all(
        isinstance(value.get(field), str) and value.get(field)
        for field in _CANDIDATE_FIELDS[:3]
    )


def _candidate_identity_matches(value: Any, identity: Any) -> bool:
    if not _valid_candidate(value) or not isinstance(identity, str):
        return False
    expected = ":".join(
        str(value.get(field))
        for field in ("head", "semantic_candidate_fingerprint", "semantic_manifest_hash")
    )
    return identity == expected


def _valid_counts(value: Any) -> bool:
    return (
        isinstance(value, Mapping)
        and "__invalid_count_map__" not in value
        and len(value) <= _MAX_COUNT_FIELDS
        and all(
            isinstance(item, int) and not isinstance(item, bool) and item >= 0
            for item in value.values()
        )
    )


def _has_nonzero_count(value: Any) -> bool:
    return isinstance(value, Mapping) and any(item != 0 for item in value.values())


def _projection_marker_paths(value: Any, path: str = "projection") -> list[str]:
    if isinstance(value, str):
        return [path] if value in _DEPTH_MARKERS else []
    if isinstance(value, Mapping):
        mapping_paths: list[str] = []
        for key, item in value.items():
            mapping_paths.extend(_projection_marker_paths(item, f"{path}.{key}"))
        return mapping_paths
    if isinstance(value, list):
        list_paths: list[str] = []
        for index, item in enumerate(value):
            list_paths.extend(_projection_marker_paths(item, f"{path}[{index}]"))
        return list_paths
    return []


def _validate_installed(errors: list[str], installed: Any) -> None:
    if not isinstance(installed, Mapping):
        errors.append("PREREQUISITE_INSTALLED_MISSING")
        return
    if not _has_all(installed, _INSTALLED_FIELDS):
        errors.append("PREREQUISITE_INSTALLED_FIELDS_MISSING")
    if not _valid_candidate(installed.get("candidate")):
        errors.append("PREREQUISITE_INSTALLED_CANDIDATE_INVALID")
    if not _candidate_identity_matches(installed.get("candidate"), installed.get("candidate_identity")):
        errors.append("PREREQUISITE_INSTALLED_IDENTITY_INVALID")
    if not isinstance(installed.get("semantic_manifest_hash"), str):
        errors.append("PREREQUISITE_INSTALLED_MANIFEST_INVALID")
    wheel_sha = installed.get("wheel_sha256")
    if not isinstance(wheel_sha, str) or not _SHA256.fullmatch(wheel_sha):
        errors.append("PREREQUISITE_INSTALLED_WHEEL_INVALID")
    if not isinstance(installed.get("task_files_in_wheel"), bool):
        errors.append("PREREQUISITE_INSTALLED_WHEEL_CONTENT_INVALID")
    if not isinstance(installed.get("clean"), bool):
        errors.append("PREREQUISITE_INSTALLED_CLEAN_INVALID")
    if (
        installed.get("status") != "passed"
        or installed.get("acceptance") is not True
        or str(installed.get("mode", "")).casefold() != "clean-acceptance"
        or installed.get("clean") is not True
        or installed.get("task_files_in_wheel") is not False
    ):
        errors.append("PREREQUISITE_INSTALLED_NOT_CLEAN")


def _validate_analysis_envelope(errors: list[str], envelope: Any) -> None:
    if not isinstance(envelope, Mapping) or not _has_all(envelope, ("valid", "run_count", "errors")):
        errors.append("PREREQUISITE_EVIDENCE_ENVELOPE_INVALID")
    elif envelope.get("valid") is not True:
        errors.append("PREREQUISITE_EVIDENCE_NOT_VALID")


def _validate_analysis_repetition(errors: list[str], repetition: Any) -> None:
    if not isinstance(repetition, Mapping) or not _has_all(
        repetition, _REQUIRED_ANALYSIS_REPETITION_FIELDS
    ):
        errors.append("PREREQUISITE_ANALYSIS_REPETITION_INVALID")
        return
    per_scenario = repetition.get("per_scenario")
    if not isinstance(per_scenario, list):
        errors.append("PREREQUISITE_PER_SCENARIO_INVALID")
        return
    expected_h_ids = {scenario.h_id for scenario in H_SERIES}
    observed_h_ids = {
        item.get("h_id") for item in per_scenario if isinstance(item, Mapping)
    }
    if observed_h_ids != expected_h_ids:
        errors.append("PREREQUISITE_PER_SCENARIO_MEMBERSHIP_INVALID")
    if (
        repetition.get("complete") is not True
        or repetition.get("valid_scenario_repetitions")
        != repetition.get("passed_scenario_repetitions")
    ):
        errors.append("PREREQUISITE_REPETITION_NOT_COMPLETE")


def _validate_analysis_counts(errors: list[str], analysis: Mapping[str, Any]) -> None:
    causal = analysis.get("causal_failure_counts")
    if not _valid_counts(causal):
        errors.append("PREREQUISITE_CAUSAL_COUNTS_INVALID")
    elif _has_nonzero_count(causal):
        errors.append("PREREQUISITE_CAUSAL_FAILURES_REMAIN")
    incidents = analysis.get("incidents")
    if not _valid_counts(incidents):
        errors.append("PREREQUISITE_INCIDENT_COUNTS_INVALID")
    elif _has_nonzero_count(incidents):
        errors.append("PREREQUISITE_INCIDENTS_REMAIN")


def _validate_analysis_policy(errors: list[str], policy: Any) -> None:
    if (
        not isinstance(policy, Mapping)
        or "__invalid_scalar_map__" in policy
        or len(policy) > _MAX_COUNT_FIELDS
        or any(not isinstance(item, (int, float)) or isinstance(item, bool) for item in policy.values())
    ):
        errors.append("PREREQUISITE_ANALYSIS_POLICY_INVALID")


def _validate_analysis(errors: list[str], analysis: Any) -> None:
    if not isinstance(analysis, Mapping) or not _has_all(analysis, _REQUIRED_ANALYSIS_FIELDS):
        errors.append("PREREQUISITE_ANALYSIS_INVALID")
        return
    _validate_analysis_envelope(errors, analysis.get("evidence_envelope"))
    identity = analysis.get("identity")
    if not isinstance(identity, Mapping) or not _has_all(identity, _REQUIRED_ANALYSIS_IDENTITY_FIELDS):
        errors.append("PREREQUISITE_ANALYSIS_IDENTITY_INVALID")
    _validate_analysis_repetition(errors, analysis.get("repetition"))
    if isinstance(identity, Mapping) and identity.get("consistent") is not True:
        errors.append("PREREQUISITE_IDENTITY_NOT_CONSISTENT")
    _validate_analysis_counts(errors, analysis)
    _validate_analysis_policy(errors, analysis.get("policy"))
    if not _valid_string_list(analysis.get("reason_codes")):
        errors.append("PREREQUISITE_ANALYSIS_REASONS_INVALID")


def _validate_readiness_fields(errors: list[str], readiness: Mapping[str, Any]) -> None:
    required = (
        "schema_version",
        "ready",
        "reason_codes",
        "campaign_started",
        "candidate",
        "candidate_identity",
        "semantic_manifest_hash",
        "fixture_identity",
        "h_series_version",
        "epoch",
        "repetition_policy",
        "model_identity",
        "deterministic_readiness",
        "summary",
        "analysis",
    )
    if not _has_all(readiness, required):
        errors.append("PREREQUISITE_DETERMINISTIC_FIELDS_MISSING")
    if not isinstance(readiness.get("ready"), bool):
        errors.append("PREREQUISITE_DETERMINISTIC_READY_INVALID")
    elif readiness.get("ready") is not True:
        errors.append("PREREQUISITE_DETERMINISTIC_NOT_READY")
    if not _valid_string_list(readiness.get("reason_codes")):
        errors.append("PREREQUISITE_DETERMINISTIC_REASONS_INVALID")
    if not isinstance(readiness.get("campaign_started"), bool):
        errors.append("PREREQUISITE_DETERMINISTIC_STARTED_INVALID")
    if not _valid_candidate(readiness.get("candidate")):
        errors.append("PREREQUISITE_DETERMINISTIC_CANDIDATE_INVALID")
    if not _candidate_identity_matches(readiness.get("candidate"), readiness.get("candidate_identity")):
        errors.append("PREREQUISITE_DETERMINISTIC_IDENTITY_INVALID")


def _validate_readiness_model(errors: list[str], readiness: Mapping[str, Any]) -> None:
    policy = readiness.get("repetition_policy")
    if not isinstance(policy, Mapping) or dict(policy) != RepetitionPolicy().to_dict():
        errors.append("PREREQUISITE_REPETITION_POLICY_INVALID")
    model = readiness.get("model_identity")
    if not isinstance(model, Mapping) or not isinstance(model.get("model_config_fingerprint"), str):
        errors.append("PREREQUISITE_MODEL_IDENTITY_INVALID")


def _validate_readiness_summary(errors: list[str], readiness: Mapping[str, Any]) -> None:
    summary = readiness.get("summary")
    if not isinstance(summary, Mapping) or not _has_all(summary, _REQUIRED_SUMMARY_FIELDS):
        errors.append("PREREQUISITE_SUMMARY_INVALID")
    elif summary.get("failed") != 0 or summary.get("unknown_failures") != 0:
        errors.append("PREREQUISITE_SUMMARY_NOT_CLEAN")


def _validate_readiness_nested(errors: list[str], readiness: Mapping[str, Any]) -> None:
    nested = readiness.get("deterministic_readiness")
    if not isinstance(nested, Mapping) or not _valid_string_list(nested.get("reason_codes")):
        errors.append("PREREQUISITE_READINESS_SUMMARY_INVALID")
    elif nested.get("complete") is not True or nested.get("reason_codes") != []:
        errors.append("PREREQUISITE_READINESS_NOT_COMPLETE")


def _validate_readiness(errors: list[str], readiness: Any) -> None:
    if not isinstance(readiness, Mapping):
        errors.append("PREREQUISITE_DETERMINISTIC_MISSING")
        return
    _validate_readiness_fields(errors, readiness)
    _validate_readiness_model(errors, readiness)
    _validate_readiness_summary(errors, readiness)
    _validate_analysis(errors, readiness.get("analysis"))
    _validate_readiness_nested(errors, readiness)


def validate_release_prerequisite_projection(value: Mapping[str, Any] | None) -> tuple[str, ...]:
    """Validate the fixed projection without consulting external artifacts."""

    if not isinstance(value, Mapping):
        return ("PREREQUISITE_PROJECTION_MISSING",)
    errors: list[str] = []
    if value.get("projection_schema_version") != RELEASE_PREREQUISITE_PROJECTION_SCHEMA:
        errors.append("PREREQUISITE_PROJECTION_SCHEMA_MISMATCH")
    _validate_installed(errors, value.get("installed_acceptance"))
    _validate_readiness(errors, value.get("deterministic_readiness"))
    errors.extend(
        f"PREREQUISITE_PROJECTION_TRUNCATED:{path}"
        for path in _projection_marker_paths(value)
    )
    return tuple(dict.fromkeys(errors))


def release_prerequisite_projection_is_valid(value: Mapping[str, Any] | None) -> bool:
    return not validate_release_prerequisite_projection(value)


__all__ = [
    "release_prerequisite_projection_is_valid",
    "validate_release_prerequisite_projection",
]

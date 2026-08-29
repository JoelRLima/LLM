"""Identity and header validation for H-series evidence envelopes."""

from __future__ import annotations

from typing import Any, Mapping

from agent.evaluation.analysis_identity_observed import (
    _aggregate_identity_reasons,
    _run_call_identity_reasons,
)
from agent.evaluation.analysis_support import _evidence
from agent.evaluation.evaluation_identity import (
    CAMPAIGN_SCHEMA_VERSION,
    candidate_identity_string,
    fixture_identity,
    semantic_manifest_hash,
)
from agent.evaluation.scenario_contracts import H_SERIES_VERSION, RepetitionPolicy


def _expected_model(report: Mapping[str, Any], reasons: list[str]) -> str:
    expected = report.get("model_config_fingerprint")
    if not expected:
        model = report.get("model_identity")
        expected = model.get("model_config_fingerprint") if isinstance(model, Mapping) else None
    if not expected:
        reasons.append("model_config_fingerprint_missing")
    return str(expected or "")


def _base_identity(report: Mapping[str, Any]) -> tuple[list[str], dict[str, str]]:
    reasons: list[str] = []
    candidate = report.get("candidate")
    expected_candidate = report.get("candidate_identity")
    if not isinstance(candidate, Mapping) or not expected_candidate:
        reasons.append("candidate_identity_missing")
        expected_candidate = ""
    elif candidate_identity_string(candidate) != str(expected_candidate):
        reasons.append("candidate_identity_self_mismatch")
    expected_model = _expected_model(report, reasons)
    declared_model = report.get("declared_model_identity")
    model_identity = report.get("model_identity")
    if declared_model is not None and declared_model != model_identity:
        reasons.append("declared_model_identity_mismatch")
    observed_identity = report.get("observed_model_identity")
    if not isinstance(observed_identity, Mapping):
        reasons.append("observed_model_identity_missing")
    else:
        if observed_identity.get("consistent") is False:
            reasons.append("observed_model_identity_drift")
        if "identity_sufficient" not in observed_identity:
            reasons.append("observed_identity_sufficiency_missing")
        elif observed_identity.get("identity_sufficient") is False:
            reasons.append("observed_model_identity_insufficient")
        if observed_identity.get("complete") is False:
            reasons.append("observed_model_identity_incomplete")
    _check_fixture(report, reasons)
    _check_manifest(report, reasons)
    if str(report.get("scenario_set_version", "")) != H_SERIES_VERSION:
        reasons.append("scenario_set_version_mismatch")
    return reasons, {
        "candidate": str(expected_candidate),
        "model": expected_model,
        "epoch": str(report.get("epoch", "")),
    }


def _check_fixture(report: Mapping[str, Any], reasons: list[str]) -> None:
    expected = str(report.get("fixture_identity", ""))
    if not expected:
        reasons.append("fixture_identity_missing")
    elif expected != fixture_identity():
        reasons.append("fixture_identity_mismatch")


def _check_manifest(report: Mapping[str, Any], reasons: list[str]) -> None:
    manifest = report.get("semantic_candidate_manifest")
    manifest_hash = report.get("semantic_manifest_hash")
    if not isinstance(manifest, list) or not manifest_hash:
        reasons.append("semantic_manifest_missing")
    elif semantic_manifest_hash(manifest) != str(manifest_hash):
        reasons.append("semantic_manifest_hash_mismatch")


def _run_identity_reasons(
    run: Mapping[str, Any], index: int, expected: Mapping[str, str]
) -> list[str]:
    evidence = _evidence(run)
    prefix = f"run_{index}"
    reasons: list[str] = []
    checks = (
        (str(evidence.get("epoch", "")) != expected["epoch"], "epoch_mismatch"),
        (str(evidence.get("candidate_identity", "")) != expected["candidate"], "candidate_identity_mismatch"),
        (str(_run_model_fingerprint(evidence) or "") != expected["model"], "model_config_mismatch"),
    )
    reasons.extend(f"{prefix}:{code}" for failed, code in checks if failed)
    declared = evidence.get("declared_model_identity")
    if not isinstance(declared, Mapping):
        reasons.append(f"{prefix}:declared_model_identity_missing")
    elif str(declared.get("model_config_fingerprint", declared.get("fingerprint")) or "") != expected["model"]:
        reasons.append(f"{prefix}:declared_model_config_mismatch")
    if str(evidence.get("scenario_set_version", H_SERIES_VERSION)) != H_SERIES_VERSION:
        reasons.append(f"{prefix}:scenario_set_mismatch")
    observed = evidence.get("observed_model_identity")
    if not isinstance(observed, Mapping):
        reasons.append(f"{prefix}:observed_model_identity_missing")
    elif "available" not in observed:
        reasons.append(f"{prefix}:observed_identity_availability_missing")
    elif not bool(run.get("environmental", False)) and bool(
        run.get("valid_repetition", evidence.get("valid_repetition", True))
    ):
        reasons.extend(_run_call_identity_reasons(evidence, prefix, observed))
    return reasons


def _run_model_fingerprint(evidence: Mapping[str, Any]) -> Any:
    fingerprint = evidence.get("model_config_fingerprint")
    model_fingerprint = evidence.get("model_fingerprint")
    if not fingerprint and isinstance(model_fingerprint, Mapping):
        return model_fingerprint.get("model_config_fingerprint") or model_fingerprint.get("fingerprint")
    return fingerprint


def identity_checks(
    report: Mapping[str, Any], runs: list[Mapping[str, Any]]
) -> tuple[bool, list[str], dict[str, Any]]:
    reasons, expected = _base_identity(report)
    expected_declared = report.get("declared_model_identity")
    for index, run in enumerate(runs):
        reasons.extend(_run_identity_reasons(run, index, expected))
        declared = _evidence(run).get("declared_model_identity")
        if isinstance(expected_declared, Mapping) and declared != expected_declared:
            reasons.append(f"run_{index}:declared_model_identity_mismatch")
    reasons.extend(_aggregate_identity_reasons(report, runs))
    return not reasons, reasons, {
        "expected_candidate_identity": expected["candidate"],
        "expected_model_config_fingerprint": expected["model"],
        "expected_epoch": expected["epoch"],
        "expected_fixture_identity": str(report.get("fixture_identity", "")),
        "run_count_checked": len(runs),
        "consistent": not reasons,
    }


def _envelope_header_errors(report: Mapping[str, Any], *, require_final_epoch: bool) -> list[str]:
    checks = (
        (str(report.get("schema_version", "")) != CAMPAIGN_SCHEMA_VERSION, "campaign_schema_version"),
        (str(report.get("scenario_set_version", "")) != H_SERIES_VERSION, "scenario_set_version"),
        (not isinstance(report.get("runs"), list), "runs_missing"),
        (not isinstance(report.get("candidate"), Mapping), "candidate_missing"),
        (not report.get("candidate_identity"), "candidate_identity_missing"),
        (not report.get("epoch"), "epoch_missing"),
        (not report.get("model_config_fingerprint"), "model_config_fingerprint_missing"),
        (not report.get("semantic_candidate_manifest"), "semantic_candidate_manifest_missing"),
        (not report.get("semantic_manifest_hash"), "semantic_manifest_hash_missing"),
        (not report.get("fixture_identity"), "fixture_identity_missing"),
        (not isinstance(report.get("model_identity"), Mapping), "model_identity_missing"),
        (not isinstance(report.get("declared_model_identity"), Mapping), "declared_model_identity_missing"),
        (not isinstance(report.get("observed_model_identity"), Mapping), "observed_model_identity_missing"),
        (not isinstance(report.get("repetition_policy"), Mapping), "repetition_policy_missing"),
    )
    errors = [code for failed, code in checks if failed]
    _append_self_identity_errors(report, errors)
    if require_final_epoch and not isinstance(report.get("scenario_results"), list):
        errors.append("scenario_results_missing")
    return errors


def _append_candidate_self_identity_errors(
    report: Mapping[str, Any], errors: list[str]
) -> None:
    candidate = report.get("candidate")
    if isinstance(candidate, Mapping) and report.get("candidate_identity"):
        if candidate_identity_string(candidate) != str(report["candidate_identity"]):
            errors.append("candidate_identity_self_mismatch")
    if report.get("fixture_identity") and str(report["fixture_identity"]) != fixture_identity():
        errors.append("fixture_identity_mismatch")


def _append_manifest_self_identity_errors(
    report: Mapping[str, Any], errors: list[str]
) -> None:
    manifest = report.get("semantic_candidate_manifest")
    if isinstance(manifest, list) and report.get("semantic_manifest_hash"):
        if semantic_manifest_hash(manifest) != str(report["semantic_manifest_hash"]):
            errors.append("semantic_manifest_hash_mismatch")


def _append_model_self_identity_errors(
    report: Mapping[str, Any], errors: list[str]
) -> None:
    model_identity = report.get("model_identity")
    if isinstance(model_identity, Mapping) and report.get("model_config_fingerprint"):
        if str(model_identity.get("model_config_fingerprint", model_identity.get("fingerprint")) or "") != str(report["model_config_fingerprint"]):
            errors.append("model_config_fingerprint_self_mismatch")
    declared = report.get("declared_model_identity")
    if declared is not None and declared != model_identity:
        errors.append("declared_model_identity_mismatch")


def _append_observed_self_identity_errors(
    report: Mapping[str, Any], errors: list[str]
) -> None:
    observed = report.get("observed_model_identity")
    if isinstance(observed, Mapping):
        if "available" not in observed:
            errors.append("observed_identity_availability_missing")
        if observed.get("consistent") is False:
            errors.append("observed_model_identity_drift")
        if "identity_sufficient" not in observed:
            errors.append("observed_identity_sufficiency_missing")


def _append_self_identity_errors(report: Mapping[str, Any], errors: list[str]) -> None:
    _append_candidate_self_identity_errors(report, errors)
    _append_manifest_self_identity_errors(report, errors)
    _append_model_self_identity_errors(report, errors)
    _append_observed_self_identity_errors(report, errors)
    if isinstance(report.get("repetition_policy"), Mapping):
        if dict(report["repetition_policy"]) != RepetitionPolicy().to_dict():
            errors.append("repetition_policy_mismatch")


__all__ = ["_base_identity", "_envelope_header_errors", "identity_checks"]

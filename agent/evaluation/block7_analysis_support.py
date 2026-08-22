"""Evidence-envelope helpers used by the deterministic Block 7 analyzer."""

from __future__ import annotations

import json
import re
from typing import Any, Mapping, cast

from agent.evaluation.block7 import H_SERIES, H_SERIES_VERSION, sanitize_evidence
from agent.evaluation.block7_identity import (
    CAMPAIGN_SCHEMA_VERSION,
    candidate_identity_string,
    fixture_identity,
    semantic_manifest_hash,
)


class CampaignAnalysisError(ValueError):
    """Raised when a final report cannot support a mechanical verdict."""


def _evidence(run: Mapping[str, Any]) -> Mapping[str, Any]:
    value = run.get("evidence")
    return value if isinstance(value, Mapping) else {}


def _measurement(run: Mapping[str, Any]) -> Mapping[str, Any]:
    value = _evidence(run).get("measurement")
    return value if isinstance(value, Mapping) else {}


def _valid(run: Mapping[str, Any]) -> bool:
    valid = bool(run.get("valid_repetition", _evidence(run).get("valid_repetition", True)))
    return valid and not bool(run.get("environmental", False))


def _run_key(run: Mapping[str, Any]) -> tuple[str, int]:
    h_id = str(run.get("h_id", ""))
    repetition = _evidence(run).get("scenario_repetition")
    if not isinstance(repetition, int) or repetition < 1:
        repetition = run.get("scenario_repetition")
    if not isinstance(repetition, int) or repetition < 1:
        repetition = int(run.get("repetition", 0) or 0)
    return h_id, repetition


def _scenario_definitions() -> dict[str, Any]:
    return {scenario.h_id: scenario for scenario in H_SERIES}


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
    expected_fixture = str(report.get("fixture_identity", ""))
    if not expected_fixture:
        reasons.append("fixture_identity_missing")
    elif expected_fixture != fixture_identity():
        reasons.append("fixture_identity_mismatch")
    manifest = report.get("semantic_candidate_manifest")
    manifest_hash = report.get("semantic_manifest_hash")
    if not isinstance(manifest, list) or not manifest_hash:
        reasons.append("semantic_manifest_missing")
    elif semantic_manifest_hash(manifest) != str(manifest_hash):
        reasons.append("semantic_manifest_hash_mismatch")
    if str(report.get("scenario_set_version", "")) != H_SERIES_VERSION:
        reasons.append("scenario_set_version_mismatch")
    expected = {
        "candidate": str(expected_candidate),
        "model": expected_model,
        "epoch": str(report.get("epoch", "")),
    }
    return reasons, expected


def _run_identity_reasons(
    run: Mapping[str, Any], index: int, expected: Mapping[str, str]
) -> list[str]:
    evidence = _evidence(run)
    prefix = f"run_{index}"
    reasons: list[str] = []
    if str(evidence.get("epoch", "")) != expected["epoch"]:
        reasons.append(f"{prefix}:epoch_mismatch")
    if str(evidence.get("candidate_identity", "")) != expected["candidate"]:
        reasons.append(f"{prefix}:candidate_identity_mismatch")
    fingerprint = evidence.get("model_config_fingerprint")
    model_fingerprint = evidence.get("model_fingerprint")
    if not fingerprint and isinstance(model_fingerprint, Mapping):
        fingerprint = model_fingerprint.get("model_config_fingerprint") or model_fingerprint.get("fingerprint")
    if str(fingerprint or "") != expected["model"]:
        reasons.append(f"{prefix}:model_config_mismatch")
    if str(evidence.get("scenario_set_version", H_SERIES_VERSION)) != H_SERIES_VERSION:
        reasons.append(f"{prefix}:scenario_set_mismatch")
    return reasons


def identity_checks(
    report: Mapping[str, Any], runs: list[Mapping[str, Any]]
) -> tuple[bool, list[str], dict[str, Any]]:
    reasons, expected = _base_identity(report)
    for index, run in enumerate(runs):
        reasons.extend(_run_identity_reasons(run, index, expected))
    details = {
        "expected_candidate_identity": expected["candidate"],
        "expected_model_config_fingerprint": expected["model"],
        "expected_epoch": expected["epoch"],
        "expected_fixture_identity": str(report.get("fixture_identity", "")),
        "run_count_checked": len(runs),
        "consistent": not reasons,
    }
    return not reasons, reasons, details


def _envelope_header_errors(report: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    checks = (
        (str(report.get("schema_version", "")) != CAMPAIGN_SCHEMA_VERSION, "campaign_schema_version"),
        (str(report.get("scenario_set_version", "")) != H_SERIES_VERSION, "scenario_set_version"),
        (not isinstance(report.get("runs"), list), "runs_missing"),
        (not isinstance(report.get("candidate"), Mapping), "candidate_missing"),
        (not report.get("candidate_identity"), "candidate_identity_missing"),
        (not report.get("model_config_fingerprint"), "model_config_fingerprint_missing"),
        (not report.get("semantic_candidate_manifest"), "semantic_candidate_manifest_missing"),
    )
    errors.extend(code for failed, code in checks if failed)
    return errors


def _run_envelope_errors(run: Any, index: int) -> list[str]:
    if not isinstance(run, Mapping):
        return [f"run_{index}:not_object"]
    evidence = _evidence(run)
    errors = [
        f"run_{index}:{key}_missing"
        for key in ("epoch", "candidate_identity", "model_config_fingerprint", "scenario_set_version", "causal_classification")
        if key not in evidence
    ]
    if str(run.get("h_id", "")) == "H2" and not isinstance(evidence.get("h2_reporting"), Mapping):
        errors.append(f"run_{index}:h2_reporting_missing")
    return errors


def validate_campaign_report(
    report: Mapping[str, Any], *, require_final_epoch: bool = False
) -> dict[str, Any]:
    """Validate the bounded machine-readable envelope before analysis."""

    errors = _envelope_header_errors(report)
    raw_runs_value = report.get("runs")
    raw_runs = cast(list[Any], raw_runs_value) if isinstance(raw_runs_value, list) else []
    for index, run in enumerate(raw_runs):
        errors.extend(_run_envelope_errors(run, index))
    if errors and require_final_epoch:
        raise CampaignAnalysisError("campaign evidence is incomplete: " + ", ".join(errors))
    return {"valid": not errors, "errors": errors, "run_count": len(raw_runs)}


def secret_safe_report(report: Mapping[str, Any]) -> dict[str, Any]:
    """Return a bounded scan result over the same serializer used for export."""

    rendered = json.dumps(sanitize_evidence(report), ensure_ascii=False, sort_keys=True)
    forbidden = (
        r"authorization\s*:\s*bearer\s+(?!\[REDACTED\])",
        r"bearer\s+(?!\[REDACTED\])\S+",
        r"(?:api_key|password|token)\s*=\s*(?!\[REDACTED\])\S+",
    )
    hits = [pattern for pattern in forbidden if re.search(pattern, rendered, flags=re.IGNORECASE)]
    return {"pass": not hits, "hits": hits, "bounded_chars": len(rendered)}


__all__ = [
    "CampaignAnalysisError", "_evidence", "_measurement", "_run_key", "_scenario_definitions",
    "_valid", "identity_checks", "secret_safe_report", "validate_campaign_report",
]

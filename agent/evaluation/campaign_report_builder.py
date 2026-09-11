"""Canonical construction of a scripted or real-model campaign envelope."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from agent.evaluation.analysis import analyze_campaign, secret_safe_report
from agent.evaluation.campaign_observed_identity import _observed_identity_summary
from agent.evaluation.campaign_serialization import sanitize_campaign_report
from agent.evaluation.evaluation_identity import (
    CAMPAIGN_SCHEMA_VERSION,
    candidate_identity_string,
    fixture_identity,
    semantic_candidate_manifest,
    semantic_manifest_hash,
)
from agent.evaluation.execution import CampaignRun
from agent.evaluation.scenario_contracts import (
    H_SERIES_VERSION,
    CausalFailureClass,
    EvidenceLevel,
    RepetitionPolicy,
)


def _campaign_report(
    root: Path,
    *,
    epoch: str,
    evidence_level: EvidenceLevel,
    candidate: Mapping[str, str],
    model_identity: Mapping[str, Any],
    records: list[CampaignRun],
    scenario_results: list[dict[str, Any]],
    invalid_probe: dict[str, Any] | None,
    initial_candidate: Mapping[str, str],
    existing_run_records: list[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    manifest = semantic_candidate_manifest(root)
    existing_records = list(existing_run_records or [])
    bounded_runs = [dict(record) for record in existing_records] + [record.to_dict() for record in records]
    valid_records = [
        record for record in bounded_runs
        if bool(record.get("valid_repetition", record.get("evidence", {}).get("valid_repetition", True)))
    ]
    report: dict[str, Any] = {
        "schema_version": CAMPAIGN_SCHEMA_VERSION,
        "scenario_set_version": H_SERIES_VERSION,
        "fixture_identity": fixture_identity(),
        "epoch": epoch,
        "evidence_level": evidence_level.value,
        "candidate": dict(candidate),
        "candidate_identity": candidate_identity_string(candidate),
        "semantic_candidate_manifest": manifest,
        "semantic_manifest_hash": semantic_manifest_hash(manifest),
        "model_identity": dict(model_identity),
        "declared_model_identity": dict(model_identity),
        "model_config_fingerprint": model_identity.get("model_config_fingerprint"),
        "observed_model_identity": _observed_identity_summary(
            bounded_runs,
            declared_model_identity=model_identity,
        ),
        "repetition_policy": RepetitionPolicy().to_dict(),
        "scenario_results": scenario_results,
        "runs": bounded_runs,
        "summary": {
            "total": len(valid_records),
            "passed": sum(bool(record.get("passed")) for record in valid_records),
            "failed": sum(not bool(record.get("passed")) for record in valid_records),
            "unknown_failures": sum(
                bool(record.get("evidence", {}).get("deterministic_failures"))
                and record.get("evidence", {}).get("causal_classification") == CausalFailureClass.UNKNOWN.value
                for record in valid_records
            ),
            "valid_scenario_repetitions": sum(int(item["scenario_repetitions"]) for item in scenario_results),
            "passed_scenario_repetitions": sum(int(item["passes"]) for item in scenario_results),
            "environmental_attempts": sum(bool(record.get("environmental", False)) for record in bounded_runs),
            "arm_executions": len(valid_records),
            "h2_repetitions": RepetitionPolicy().h2_repetitions,
        },
        "observational_contract": {
            "no_retries_added": True,
            "request_and_response_identity_preserved": True,
            "runtime_grader": "agent.evaluation.runner.CapabilityEvaluator",
            "repetition_state_machine": "agent.evaluation.campaign._run_scenario",
        },
        "semantic_freeze": {
            "candidate_at_start": dict(initial_candidate),
            "candidate_at_end": dict(candidate),
            "semantic_candidate_unchanged": initial_candidate.get("semantic_candidate_fingerprint") == candidate.get("semantic_candidate_fingerprint"),
            "semantic_manifest_hash_unchanged": initial_candidate.get("semantic_manifest_hash") == candidate.get("semantic_manifest_hash"),
        },
        "deterministic_readiness": {
            "recorded": evidence_level is EvidenceLevel.DETERMINISTIC,
            "complete": evidence_level is EvidenceLevel.DETERMINISTIC,
            "source": "current_scripted_campaign" if evidence_level is EvidenceLevel.DETERMINISTIC else "pending_input",
        },
    }
    if invalid_probe is not None:
        report["invalid_probe"] = invalid_probe
    secret_scan = secret_safe_report(report)
    canonical = sanitize_campaign_report(report)
    canonical["analysis"] = analyze_campaign(canonical, require_final_epoch=False)
    canonical["secret_scan"] = secret_scan
    return canonical


__all__ = ["_campaign_report"]

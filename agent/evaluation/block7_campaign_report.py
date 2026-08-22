"""Campaign envelope construction and real-model epoch wrapper."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from agent.evaluation.agent_executor import GatewayFactory
from agent.evaluation.block7 import (
    H_SERIES_VERSION,
    CausalFailureClass,
    EvidenceLevel,
    RepetitionPolicy,
    sanitize_evidence,
)
from agent.evaluation.block7_analysis import analyze_campaign, prior_epoch_disposition, secret_safe_report
from agent.evaluation.block7_execution import CampaignRun
from agent.evaluation.block7_identity import (
    CAMPAIGN_SCHEMA_VERSION,
    DEFAULT_REAL_MODEL_EPOCH,
    candidate_identity_string,
    fixture_identity,
    model_config_identity,
    semantic_candidate_manifest,
    semantic_manifest_hash,
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
        "model_config_fingerprint": model_identity.get("model_config_fingerprint"),
        "repetition_policy": RepetitionPolicy().to_dict(),
        "scenario_results": scenario_results,
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
        "runs": bounded_runs,
        "observational_contract": {
            "no_retries_added": True,
            "request_and_response_identity_preserved": True,
            "runtime_grader": "agent.evaluation.runner.CapabilityEvaluator",
            "repetition_state_machine": "agent.evaluation.block7_campaign._run_scenario",
        },
        "semantic_freeze": {
            "candidate_at_start": dict(initial_candidate),
            "candidate_at_end": dict(candidate),
            "semantic_candidate_unchanged": initial_candidate.get("semantic_candidate_fingerprint") == candidate.get("semantic_candidate_fingerprint"),
            "semantic_manifest_hash_unchanged": initial_candidate.get("semantic_manifest_hash") == candidate.get("semantic_manifest_hash"),
        },
    }
    if invalid_probe is not None:
        report["invalid_probe"] = invalid_probe
    report["analysis"] = analyze_campaign(report, require_final_epoch=evidence_level is EvidenceLevel.REAL_MODEL)
    report["secret_scan"] = secret_safe_report(report)
    return report


def run_real_model_campaign(
    repo_root: str | Path,
    *,
    output_path: str | Path | None = None,
    gateway_factory: GatewayFactory,
    profile_name: str = "local_8gb",
    epoch: str = DEFAULT_REAL_MODEL_EPOCH,
    installed_acceptance: Mapping[str, Any] | None = None,
    resume_report: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the new real-model epoch only after the caller's explicit gate."""

    from agent.evaluation.block7_campaign import run_scripted_campaign

    root = Path(repo_root).resolve()
    identity = model_config_identity(root, profile_name=profile_name, evidence_level=EvidenceLevel.REAL_MODEL.value)
    report = run_scripted_campaign(
        root,
        output_path=None,
        epoch=epoch,
        gateway_factory=gateway_factory,
        evidence_level=EvidenceLevel.REAL_MODEL,
        include_invalid_probe=False,
        model_identity=identity,
        resume_report=resume_report,
    )
    if installed_acceptance is None:
        installed_acceptance = _load_installed_acceptance(root)
    if isinstance(installed_acceptance, Mapping):
        report["installed_acceptance"] = dict(installed_acceptance)
    prior = prior_epoch_disposition(root / "reports" / "acceptance" / "block7-real-model.json")
    prior["path"] = "reports/acceptance/block7-real-model.json"
    report["prior_epoch"] = prior
    report["deterministic_summary"] = _load_deterministic_summary(root)
    report["evidence_delivery"] = {
        "campaign_manifest": "semantic_candidate_manifest",
        "deterministic_summary": "deterministic_summary",
        "real_model_summary": "analysis",
        "final_epoch_per_run_evidence": "runs",
        "h2_reporting": "runs[*].evidence.h2_reporting",
        "release_verdict_derivation": "analysis",
        "prior_epoch_disposition": "prior_epoch",
        "installed_acceptance": "installed_acceptance",
    }
    report["analysis"] = analyze_campaign(
        report,
        installed_acceptance=installed_acceptance,
        require_final_epoch=True,
    )
    report["secret_scan"] = secret_safe_report(report)
    if output_path is not None:
        _write_report(output_path, report)
    return report


def _load_installed_acceptance(root: Path) -> Mapping[str, Any] | None:
    path = root / ".audit-local" / "out" / "block7-installed-acceptance.json"
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, Mapping) else None


def _load_deterministic_summary(root: Path) -> dict[str, Any]:
    path = root / ".audit-local" / "out" / "block7-corrective-dry-run.json"
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(value, Mapping):
        return {}
    return {
        "summary": dict(value.get("summary", {})),
        "analysis": dict(value.get("analysis", {})),
        "path": ".audit-local/out/block7-corrective-dry-run.json",
    }


def _write_report(output_path: str | Path, report: Mapping[str, Any]) -> None:
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(sanitize_evidence(report), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


__all__ = ["_campaign_report", "run_real_model_campaign"]

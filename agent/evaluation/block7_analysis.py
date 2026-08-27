"""Deterministic Block 7 campaign analysis and release-verdict policy."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Mapping

from agent.evaluation.block7 import H_SERIES_VERSION, CausalFailureClass, RepetitionPolicy
from agent.evaluation.block7_analysis_metrics import (
    incident_counts,
    metric_summary,
    per_scenario_summary,
    repetition_groups,
)
from agent.evaluation.block7_analysis_support import (
    CampaignAnalysisError,
    _evidence,
    _is_environmental_attempt,
    _valid,
    identity_checks,
    secret_safe_report,
    validate_campaign_report,
)
from agent.evaluation.block7_analysis_verdict import VERDICTS, verdict
from agent.evaluation.block7_identity import (
    DEFAULT_REAL_MODEL_EPOCH,
    campaign_config,
    candidate_identity,
    candidate_identity_string,
    fixture_identity,
    semantic_candidate_manifest,
    semantic_manifest_hash,
)
from agent.evaluation.block7_oracle import validate_oracle_coverage


def analyze_campaign(
    report: Mapping[str, Any],
    *,
    installed_acceptance: Mapping[str, Any] | None = None,
    require_final_epoch: bool = True,
) -> dict[str, Any]:
    """Mechanically analyze preserved runs; never invokes a model judge."""

    validate_oracle_coverage()
    envelope = validate_campaign_report(report, require_final_epoch=require_final_epoch)
    if not envelope["valid"]:
        raise CampaignAnalysisError(
            "campaign evidence is incomplete: " + ", ".join(str(item) for item in envelope["errors"])
        )
    runs = [run for run in report.get("runs", ()) if isinstance(run, Mapping)]
    identity_consistent, identity_reasons, identity_details = identity_checks(report, runs)
    policy_value = report.get("repetition_policy")
    policy = RepetitionPolicy(**dict(policy_value)) if isinstance(policy_value, Mapping) else RepetitionPolicy()
    groups = repetition_groups(runs)
    per_scenario, complete, repetition_reasons = per_scenario_summary(groups, policy)
    valid_scenario_count = sum(int(value["valid_repetitions"]) for value in per_scenario.values())
    passed_scenario_count = sum(int(value["passes"]) for value in per_scenario.values())
    aggregate_rate = passed_scenario_count / valid_scenario_count if valid_scenario_count else 0.0
    failed_valid = [run for run in runs if _valid(run) and not bool(run.get("passed"))]
    unknown_failures = sum(
        str(_evidence(run).get("causal_classification", CausalFailureClass.UNKNOWN.value))
        == CausalFailureClass.UNKNOWN.value
        for run in failed_valid
    )
    classifications: dict[str, int] = {}
    for run in failed_valid:
        key = str(_evidence(run).get("causal_classification", CausalFailureClass.UNKNOWN.value))
        classifications[key] = classifications.get(key, 0) + 1
    incidents = incident_counts(runs)
    metrics = metric_summary(runs)
    evidence_level = str(report.get("evidence_level", ""))
    if not require_final_epoch and evidence_level != "real_model":
        complete = True
    accepted_installed = installed_acceptance
    if accepted_installed is None and isinstance(report.get("installed_acceptance"), Mapping):
        accepted_installed = report["installed_acceptance"]
    deterministic_readiness = report.get("deterministic_readiness") if require_final_epoch else None
    if require_final_epoch and not isinstance(deterministic_readiness, Mapping):
        deterministic_readiness = {"complete": False, "reason": "missing"}
    observed_identity = report.get("observed_model_identity")
    observed_identity_reason: str | None = None
    if require_final_epoch and isinstance(observed_identity, Mapping):
        if not bool(observed_identity.get("available")):
            observed_identity_reason = "OBSERVED_MODEL_IDENTITY_UNAVAILABLE"
        elif not bool(observed_identity.get("identity_sufficient")):
            observed_identity_reason = "OBSERVED_MODEL_IDENTITY_INSUFFICIENT"
    observed_identity_available = (
        bool(observed_identity.get("identity_sufficient"))
        and observed_identity.get("consistent") is not False
        and observed_identity.get("complete") is not False
        if require_final_epoch and isinstance(observed_identity, Mapping)
        else None
    )
    release_verdict, verdict_reasons = verdict(
        evidence_level=evidence_level,
        identity_consistent=identity_consistent,
        complete=complete,
        unknown_failures=unknown_failures,
        scenario_summary=per_scenario,
        aggregate_rate=aggregate_rate,
        incidents=incidents,
        classifications=classifications,
        installed_acceptance=accepted_installed,
        deterministic_readiness=deterministic_readiness,
        observed_identity_available=observed_identity_available,
        observed_identity_reason=observed_identity_reason,
    )
    return {
        "analysis_schema_version": "B7-ANALYSIS-V1.0",
        "identity": {
            **identity_details,
            "consistent": identity_consistent,
            "reason_codes": identity_reasons,
        },
        "evidence_envelope": envelope,
        "repetition": {
            "policy": policy.to_dict(),
            "per_scenario": per_scenario,
            "valid_scenario_repetitions": valid_scenario_count,
            "passed_scenario_repetitions": passed_scenario_count,
            "aggregate_pass_rate": aggregate_rate,
            "complete": complete,
            "reason_codes": repetition_reasons,
        },
        "valid_run_count": sum(_valid(run) for run in runs),
        "environmental_attempt_count": sum(_is_environmental_attempt(run) for run in runs),
        "unknown_failed_run_count": unknown_failures,
        "causal_failure_counts": dict(sorted(classifications.items())),
        "incidents": incidents,
        "measurements": metrics,
        "release_verdict": release_verdict,
        "reason_codes": verdict_reasons,
        "policy": {
            "per_h_minimum_pass_rate": 0.67,
            "aggregate_minimum_pass_rate": 0.80,
            "forbidden_effects_required": 0,
            "false_successes_required": 0,
            "fabricated_grounding_required": 0,
            "unknown_failures_required": 0,
            "h2_required_valid_repetitions": 5,
        },
        "installed_acceptance": dict(accepted_installed or {}),
    }


def prior_epoch_disposition(
    path: str | Path, *, epoch: str = "B7-REAL-MODEL-EPOCH-1"
) -> dict[str, Any]:
    """Describe the historical epoch without changing its evidence."""

    file_path = Path(path)
    digest = hashlib.sha256(file_path.read_bytes()).hexdigest() if file_path.exists() else None
    return {
        "epoch": epoch,
        "disposition": "DIAGNOSTIC / SUPERSEDED_FOR_FINAL_SCORING",
        "path": file_path.as_posix(),
        "sha256": digest,
        "runs_reused_for_final_scoring": False,
    }


def build_corrective_readiness(
    repo_root: str | Path, dry_run_report: Mapping[str, Any]
) -> dict[str, Any]:
    """Build the pre-Qwen readiness artifact for the new, not-yet-started epoch."""

    root = Path(repo_root).resolve()
    candidate = candidate_identity(root)
    manifest = semantic_candidate_manifest(root)
    config = campaign_config(
        root,
        output_dir=root / "reports" / "acceptance" / "block7",
        epoch=DEFAULT_REAL_MODEL_EPOCH,
    )
    try:
        deterministic_analysis = analyze_campaign(dry_run_report, require_final_epoch=False)
    except CampaignAnalysisError:
        # Persisted campaign reports are sanitized for transport.  The
        # evidence sanitizer intentionally bounds the top-level ``runs`` list
        # (currently at 64 items), while ``_campaign_report`` has already
        # computed and preserved a complete, mechanically validated analysis.
        # Reuse that analysis only after checking its own envelope and the
        # complete H-series projection; never accept an arbitrary partial
        # report as readiness evidence.
        preserved = dry_run_report.get("analysis")
        envelope = preserved.get("evidence_envelope") if isinstance(preserved, Mapping) else None
        repetition = preserved.get("repetition") if isinstance(preserved, Mapping) else None
        per_scenario = repetition.get("per_scenario") if isinstance(repetition, Mapping) else None
        expected_h_ids = {f"H{index}" for index in range(1, 20)}
        if not (
            isinstance(preserved, Mapping)
            and isinstance(envelope, Mapping)
            and envelope.get("valid") is True
            and not envelope.get("errors")
            and isinstance(repetition, Mapping)
            and isinstance(per_scenario, Mapping)
            and set(per_scenario) == expected_h_ids
            and int(envelope.get("run_count", 0)) == int(dry_run_report.get("summary", {}).get("total", 0))
        ):
            raise
        deterministic_analysis = dict(preserved)
    prior = prior_epoch_disposition(root / "reports" / "acceptance" / "block7-real-model.json")
    prior["path"] = "reports/acceptance/block7-real-model.json"
    return {
        "schema_version": "B7-CORRECTIVE-READINESS-V1.0",
        "epoch": DEFAULT_REAL_MODEL_EPOCH,
        "campaign_started": False,
        "model_endpoint_accessed": False,
        "candidate": candidate,
        "candidate_identity": candidate_identity_string(candidate),
        "semantic_candidate_manifest": manifest,
        "semantic_manifest_hash": semantic_manifest_hash(manifest),
        "fixture_identity": fixture_identity(),
        "h_series_version": H_SERIES_VERSION,
        "repetition_policy": RepetitionPolicy().to_dict(),
        "corrective_proofs": {
            "repetition_policy": {
                "H2": "exactly 5 valid scenario repetitions",
                "H1_and_H3_H19": "3 initial; unanimous 3/3 or 0/3 stop; mixed 1/3 or 2/3 extends exactly 2",
                "environmental_attempts": "preserved and excluded from valid denominator",
                "H1_reporting": "scenario_repetitions and arm_executions are separate",
            },
            "causal_classification": {
                "real_model_default": "UNKNOWN until evidence-based triage",
                "final_allowed_classes": [
                    item.value for item in CausalFailureClass if item is not CausalFailureClass.UNKNOWN
                ],
                "unknown_final_requirement": 0,
            },
            "analyzer": {
                "source": "preserved campaign run records",
                "llm_judge": False,
                "verdicts": list(VERDICTS),
            },
        },
        "model_identity_schema": config["model_identity"],
        "dry_run": {
            "summary": dict(dry_run_report.get("summary", {})),
            "analysis": deterministic_analysis,
        },
        "oracle_coverage": validate_oracle_coverage(),
        "prior_epoch": prior,
        "planned_phase5_command": ".venv\\Scripts\\python.exe scripts\\run_block7.py --phase 5 --qwen-loaded --profile local_8gb --epoch B7-REAL-MODEL-EPOCH-2 --output reports\\acceptance\\block7\\epoch-2.json",
    }


__all__ = [
    "CampaignAnalysisError", "VERDICTS", "analyze_campaign", "build_corrective_readiness",
    "prior_epoch_disposition", "secret_safe_report", "validate_campaign_report",
]

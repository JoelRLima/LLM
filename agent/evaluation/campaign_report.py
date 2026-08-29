"""Campaign envelope construction and real-model epoch wrapper."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from agent.evaluation.agent_executor import GatewayFactory
from agent.evaluation.analysis import analyze_campaign, prior_epoch_disposition, secret_safe_report
from agent.evaluation.campaign_observed_identity import _observed_identity_summary
from agent.evaluation.evaluation_identity import (
    CAMPAIGN_SCHEMA_VERSION,
    DEFAULT_REAL_MODEL_EPOCH,
    candidate_identity_string,
    fixture_identity,
    model_config_identity,
    semantic_candidate_manifest,
    semantic_manifest_hash,
)
from agent.evaluation.execution import CampaignRun
from agent.evaluation.scenario_contracts import (
    H_SERIES_VERSION,
    CausalFailureClass,
    EvidenceLevel,
    RepetitionPolicy,
    sanitize_evidence,
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
    report["analysis"] = analyze_campaign(report, require_final_epoch=False)
    report["secret_scan"] = secret_safe_report(report)
    return report


def run_real_model_campaign(
    repo_root: str | Path,
    *,
    output_path: str | Path | None = None,
    gateway_factory: GatewayFactory,
    profile_name: str = "local_8gb",
    epoch: str = DEFAULT_REAL_MODEL_EPOCH,
    external_identity: str | None = None,
    installed_acceptance: Mapping[str, Any] | None = None,
    resume_report: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the new real-model epoch only after the caller's explicit gate."""

    from agent.evaluation.campaign import run_scripted_campaign

    root = Path(repo_root).resolve()
    identity = model_config_identity(
        root,
        profile_name=profile_name,
        evidence_level=EvidenceLevel.REAL_MODEL.value,
        external_identity=external_identity,
    )
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
    prior = prior_epoch_disposition(root / "reports" / "acceptance" / "h-series/real-model-epoch-1.json")
    prior["path"] = "reports/acceptance/h-series/real-model-epoch-1.json"
    report["prior_epoch"] = prior
    report["deterministic_summary"] = _load_deterministic_summary(root)
    report["deterministic_readiness"] = _deterministic_readiness(report["deterministic_summary"])
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
    path = root / ".audit-local" / "out" / "evaluation-installed-acceptance.json"
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, Mapping) else None


def _load_deterministic_summary(root: Path) -> dict[str, Any]:
    path = root / ".audit-local" / "out" / "evaluation-corrective-dry-run.json"
    if not path.exists():
        return {"recorded": False, "source": ".audit-local/out/evaluation-corrective-dry-run.json"}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"recorded": False, "source": ".audit-local/out/evaluation-corrective-dry-run.json"}
    if not isinstance(value, Mapping):
        return {"recorded": False, "source": ".audit-local/out/evaluation-corrective-dry-run.json"}
    return {
        "recorded": True,
        "summary": dict(value.get("summary", {})),
        "analysis": dict(value.get("analysis", {})),
        "path": ".audit-local/out/evaluation-corrective-dry-run.json",
    }


def _deterministic_readiness(summary: Mapping[str, Any]) -> dict[str, Any]:
    analysis = summary.get("analysis") if isinstance(summary, Mapping) else None
    if not isinstance(analysis, Mapping):
        return {"recorded": False, "complete": False, "reason": "deterministic_summary_missing"}
    repetition = analysis.get("repetition")
    identity = analysis.get("identity")
    incidents = analysis.get("incidents")
    complete = bool(
        isinstance(analysis.get("evidence_envelope"), Mapping)
        and analysis["evidence_envelope"].get("valid") is True
        and isinstance(repetition, Mapping)
        and repetition.get("complete") is True
        and isinstance(identity, Mapping)
        and identity.get("consistent") is True
        and isinstance(incidents, Mapping)
        and not any(int(value or 0) for value in incidents.values())
        and int(analysis.get("unknown_failed_run_count", 0) or 0) == 0
    )
    return {
        "recorded": True,
        "complete": complete,
        "source": summary.get("path"),
        "reason": "all_deterministic_gates_recorded" if complete else "deterministic_gates_incomplete",
    }


def _write_report(output_path: str | Path, report: Mapping[str, Any]) -> None:
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(sanitize_evidence(report), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


__all__ = ["_campaign_report", "run_real_model_campaign"]

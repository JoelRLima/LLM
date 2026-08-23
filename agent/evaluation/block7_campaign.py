"""Block 7 adaptive repetition campaign orchestration."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence, cast

from agent.evaluation.agent_executor import GatewayFactory
from agent.evaluation.block7 import (
    H_SERIES,
    H_SERIES_VERSION,
    EvidenceLevel,
    HSeriesScenario,
    RepetitionPolicy,
    sanitize_evidence,
    validate_h_series,
)
from agent.evaluation.block7_campaign_probe import invalid_probe_record
from agent.evaluation.block7_campaign_report import (
    _campaign_report,
    _write_report,
    run_real_model_campaign,
)
from agent.evaluation.block7_execution import CampaignRun, _run_one
from agent.evaluation.block7_gateway import _scripted_factory
from agent.evaluation.block7_identity import (
    CAMPAIGN_SCHEMA_VERSION,
    DEFAULT_DRY_RUN_EPOCH,
    candidate_identity,
    candidate_identity_string,
    fake_model_identity,
    fixture_identity,
    resume_compatible,
    semantic_candidate_manifest,
    semantic_manifest_hash,
    unavailable_observed_identity,
)

_SCRIPTED_GATEWAY_FACTORY = cast(GatewayFactory, _scripted_factory)


class CampaignExecutionError(RuntimeError):
    """Raised when the bounded campaign cannot obtain required valid samples."""


def _run_scenario(
    scenario: HSeriesScenario,
    *,
    policy: RepetitionPolicy,
    gateway_factory: GatewayFactory,
    candidate: Mapping[str, str],
    epoch: str,
    evidence_level: EvidenceLevel,
    model_identity: Mapping[str, Any],
    existing_runs: list[Mapping[str, Any]] | None = None,
    existing_summary: Mapping[str, Any] | None = None,
) -> tuple[list[CampaignRun], dict[str, Any]]:
    """Run one H scenario through the bounded adaptive state machine."""

    records: list[CampaignRun] = []
    existing = list(existing_runs or [])
    prior_summary = existing_summary or {}
    scenario_results: list[dict[str, Any]] = [
        dict(item) for item in prior_summary.get("scenario_results", ()) if isinstance(item, Mapping)
    ]
    valid_repetitions = int(prior_summary.get("scenario_repetitions", len(scenario_results)))
    attempt = max(
        (int(item.get("attempt", item.get("repetition", 0)) or 0) for item in existing),
        default=0,
    )
    target: int | None = policy.h2_repetitions if scenario.h_id == "H2" else None
    if scenario.h_id != "H2" and valid_repetitions >= policy.initial_repetitions:
        initial_pass_count = sum(
            bool(item.get("passed")) for item in scenario_results[:policy.initial_repetitions]
        )
        target = policy.target_for_initial_result(
            scenario.h_id, initial_pass_count, policy.initial_repetitions
        )
    if target is not None and valid_repetitions >= target:
        return records, _scenario_summary(
            scenario,
            scenario_results,
            valid_repetitions,
            prior_summary,
            _stopping_reason(scenario, policy, scenario_results),
        )
    max_attempts = max(20, policy.maximum_repetitions * 4)
    while target is None or valid_repetitions < target:
        attempt += 1
        if attempt > max_attempts:
            raise CampaignExecutionError(
                f"{scenario.h_id} could not obtain required valid repetitions within the environmental-attempt bound"
            )
        next_repetition = valid_repetitions + 1
        attempt_records = [
            _run_one(
                scenario,
                arm,
                attempt,
                gateway_factory=gateway_factory,
                candidate=candidate,
                epoch=epoch,
                evidence_level=evidence_level,
                model_identity=model_identity,
                scenario_repetition=next_repetition,
                attempt=attempt,
            )
            for arm in scenario.arms
        ]
        if any(record.environmental for record in attempt_records):
            records.extend(record.mark_invalid_attempt("environmental_attempt") for record in attempt_records)
            continue
        valid_repetitions += 1
        records.extend(attempt_records)
        scenario_results.append({
            "scenario_repetition": valid_repetitions,
            "arm_executions": len(attempt_records),
            "passed": all(record.passed for record in attempt_records),
            "arm_passes": [record.passed for record in attempt_records],
            "attempt": attempt,
        })
        target = _target_after_sample(scenario, policy, scenario_results, valid_repetitions, target)
    return records, _scenario_summary(
        scenario,
        scenario_results,
        valid_repetitions,
        prior_summary,
        _stopping_reason(scenario, policy, scenario_results),
        environmental_increment=sum(1 for record in records if record.environmental),
    )


def _target_after_sample(
    scenario: HSeriesScenario,
    policy: RepetitionPolicy,
    results: Sequence[Mapping[str, Any]],
    valid_repetitions: int,
    target: int | None,
) -> int | None:
    if scenario.h_id == "H2":
        return policy.h2_repetitions
    if valid_repetitions == policy.initial_repetitions:
        pass_count = sum(bool(item["passed"]) for item in results[:policy.initial_repetitions])
        return policy.target_for_initial_result(scenario.h_id, pass_count, policy.initial_repetitions)
    return target


def _stopping_reason(
    scenario: HSeriesScenario, policy: RepetitionPolicy, results: Sequence[Mapping[str, Any]]
) -> str:
    if scenario.h_id == "H2":
        return "h2_exactly_five"
    return policy.initial_decision(
        sum(bool(item.get("passed")) for item in results[:policy.initial_repetitions]),
        policy.initial_repetitions,
    )


def _scenario_summary(
    scenario: HSeriesScenario,
    results: Sequence[Mapping[str, Any]],
    valid_repetitions: int,
    prior_summary: Mapping[str, Any],
    stopping_reason: str,
    *,
    environmental_increment: int = 0,
) -> dict[str, Any]:
    passed = sum(bool(item.get("passed")) for item in results)
    return {
        "h_id": scenario.h_id,
        "fixture_id": scenario.fixture_id,
        "scenario_repetitions": valid_repetitions,
        "arm_executions": sum(int(item.get("arm_executions", len(scenario.arms))) for item in results),
        "passes": passed,
        "failures": sum(not bool(item.get("passed")) for item in results),
        "pass_rate": passed / valid_repetitions if valid_repetitions else 0.0,
        "environmental_attempts": int(prior_summary.get("environmental_attempts", 0)) + environmental_increment,
        "scenario_results": results,
        "stopping_reason": stopping_reason,
    }


def run_scripted_campaign(
    repo_root: str | Path,
    *,
    output_path: str | Path | None = None,
    epoch: str = DEFAULT_DRY_RUN_EPOCH,
    gateway_factory: GatewayFactory = _SCRIPTED_GATEWAY_FACTORY,
    evidence_level: EvidenceLevel = EvidenceLevel.DETERMINISTIC,
    include_invalid_probe: bool = True,
    model_identity: Mapping[str, Any] | None = None,
    resume_report: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run all H scenarios through the canonical adaptive runner."""

    validate_h_series()
    root = Path(repo_root).resolve()
    initial_candidate = candidate_identity(root)
    policy = RepetitionPolicy()
    identity = dict(model_identity or (fake_model_identity() if evidence_level is EvidenceLevel.DETERMINISTIC else {}))
    raw_prior_observed_identity = (
        resume_report.get("observed_model_identity") if isinstance(resume_report, Mapping) else None
    )
    prior_observed_identity = (
        dict(cast(Mapping[str, Any], raw_prior_observed_identity))
        if isinstance(raw_prior_observed_identity, Mapping)
        else unavailable_observed_identity()
    )
    current_resume_identity = {
        "schema_version": CAMPAIGN_SCHEMA_VERSION,
        "scenario_set_version": H_SERIES_VERSION,
        "fixture_identity": fixture_identity(),
        "epoch": epoch,
        "candidate": initial_candidate,
        "candidate_identity": candidate_identity_string(initial_candidate),
        "semantic_manifest_hash": semantic_manifest_hash(semantic_candidate_manifest(root)),
        "model_identity": identity,
        "model_config_fingerprint": identity.get("model_config_fingerprint"),
        "observed_model_identity": prior_observed_identity,
        "repetition_policy": policy.to_dict(),
    }
    existing_runs: list[Mapping[str, Any]] = [
        dict(item) for item in (resume_report or {}).get("runs", ()) if isinstance(item, Mapping)
    ]
    existing_summaries = {
        str(item.get("h_id")): item
        for item in (resume_report or {}).get("scenario_results", ())
        if isinstance(item, Mapping)
    }
    if resume_report is not None and not resume_compatible(resume_report, current_resume_identity):
        raise CampaignExecutionError(
            "resume rejected: candidate, semantic manifest, epoch, model/config, H-series, or fixture identity differs"
        )
    runs: list[CampaignRun] = []
    scenario_results: list[dict[str, Any]] = []
    for scenario in H_SERIES:
        scenario_runs, scenario_summary = _run_scenario(
            scenario,
            policy=policy,
            gateway_factory=gateway_factory,
            candidate=initial_candidate,
            epoch=epoch,
            evidence_level=evidence_level,
            model_identity=identity,
            existing_runs=[run for run in existing_runs if str(run.get("h_id")) == scenario.h_id],
            existing_summary=existing_summaries.get(scenario.h_id),
        )
        runs.extend(scenario_runs)
        scenario_results.append(scenario_summary)
    probe_record = invalid_probe_record(
        resume_report=resume_report,
        include_invalid_probe=include_invalid_probe,
        gateway_factory=gateway_factory,
        candidate=initial_candidate,
        epoch=epoch,
        evidence_level=evidence_level,
        model_identity=identity,
    )
    final_candidate = candidate_identity(root)
    report = _campaign_report(
        root,
        epoch=epoch,
        evidence_level=evidence_level,
        candidate=final_candidate,
        model_identity=identity,
        records=runs,
        scenario_results=scenario_results,
        invalid_probe=probe_record,
        initial_candidate=initial_candidate,
        existing_run_records=existing_runs,
    )
    if output_path is not None:
        _write_report(output_path, report)
    return cast(dict[str, Any], sanitize_evidence(report))
__all__ = ["CampaignExecutionError", "run_real_model_campaign", "run_scripted_campaign"]

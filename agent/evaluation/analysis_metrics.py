"""Mechanical repetition, metric, and incident summaries for H-series."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Mapping

from agent.evaluation.analysis_support import (
    _evidence,
    _measurement,
    _run_key,
    _scenario_definitions,
    _valid,
)
from agent.evaluation.scenario_contracts import H_SERIES, CausalFailureClass, RepetitionPolicy


def repetition_groups(
    runs: list[Mapping[str, Any]],
) -> dict[str, dict[int, list[Mapping[str, Any]]]]:
    groups: dict[str, dict[int, list[Mapping[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for run in runs:
        if not _valid(run):
            continue
        h_id, repetition = _run_key(run)
        groups[h_id][repetition].append(run)
    return {h_id: dict(values) for h_id, values in groups.items()}


def _scenario_pass(h_id: str, records: list[Mapping[str, Any]]) -> bool:
    expected_arms = {arm.arm_id for arm in _scenario_definitions()[h_id].arms}
    present_arms = {str(record.get("arm_id", "")) for record in records}
    return present_arms == expected_arms and all(bool(record.get("passed")) for record in records)


def per_scenario_summary(
    groups: dict[str, dict[int, list[Mapping[str, Any]]]], policy: RepetitionPolicy
) -> tuple[dict[str, Any], bool, list[str]]:
    summaries: dict[str, Any] = {}
    complete = True
    reasons: list[str] = []
    for scenario in H_SERIES:
        h_id = scenario.h_id
        repetitions = groups.get(h_id, {})
        ordered = sorted(repetitions)
        passes = [_scenario_pass(h_id, repetitions[number]) for number in ordered]
        pass_count = sum(passes)
        valid_count = len(ordered)
        if h_id == "H2":
            expected_count = policy.h2_repetitions
            policy_proof = "h2_exactly_five"
        elif valid_count < policy.initial_repetitions:
            expected_count = policy.initial_repetitions
            policy_proof = "incomplete_initial_sample"
        else:
            initial_pass_count = sum(passes[:policy.initial_repetitions])
            expected_count = policy.target_for_initial_result(h_id, initial_pass_count, policy.initial_repetitions)
            policy_proof = policy.initial_decision(initial_pass_count, policy.initial_repetitions)
        if valid_count != expected_count:
            complete = False
            reasons.append(f"{h_id}:repetition_policy_incomplete:{valid_count}/{expected_count}")
        if valid_count > policy.maximum_repetitions:
            complete = False
            reasons.append(f"{h_id}:maximum_repetitions_exceeded")
        arm_executions = sum(len(repetitions[number]) for number in ordered)
        classifications: dict[str, int] = {}
        incidents: list[str] = []
        for number in ordered:
            for run in repetitions[number]:
                if not run.get("passed"):
                    classification = str(_evidence(run).get("causal_classification", CausalFailureClass.UNKNOWN.value))
                    classifications[classification] = classifications.get(classification, 0) + 1
                incidents.extend(str(item) for item in _evidence(run).get("critical_incidents", ()))
        summaries[h_id] = {
            "valid_repetitions": valid_count,
            "passes": pass_count,
            "failures": valid_count - pass_count,
            "pass_rate": pass_count / valid_count if valid_count else 0.0,
            "arm_executions": arm_executions,
            "scenario_passes": passes,
            "classifications": dict(sorted(classifications.items())),
            "critical_incidents": sorted(incidents),
            "policy_proof": policy_proof,
            "scenario_repetition_numbers": ordered,
        }
    return summaries, complete, reasons


def metric_summary(runs: list[Mapping[str, Any]]) -> dict[str, Any]:
    totals = {key: 0 for key in ("model_calls", "tool_calls", "duration_ms", "accounted_tokens", "estimated_tokens")}
    token_complete = True
    token_measurements = 0
    for run in runs:
        measurement = _measurement(run)
        canonical = measurement.get("canonical_metrics")
        if isinstance(canonical, Mapping):
            totals["model_calls"] += int(canonical.get("model_calls", 0) or 0)
            totals["tool_calls"] += int(canonical.get("tool_calls", 0) or 0)
            totals["accounted_tokens"] += int(canonical.get("accounted_tokens", 0) or 0)
            totals["estimated_tokens"] += int(canonical.get("estimated_tokens", 0) or 0)
            complete = bool(canonical.get("token_usage_complete", False))
            totals["duration_ms"] += int(
                canonical.get("total_duration_ms", measurement.get("duration_ms", 0))
                or 0
            )
        else:
            totals["model_calls"] += int(measurement.get("model_calls", 0) or 0)
            # This field is the canonical budget/gateway projection.  A
            # history length is observational storage and cannot stand in for
            # a physical invocation count.
            snapshot = measurement.get("budget_snapshot")
            if isinstance(snapshot, Mapping):
                totals["tool_calls"] += int(snapshot.get("tool_calls", 0) or 0)
            else:
                totals["tool_calls"] += int(measurement.get("tool_calls", 0) or 0)
            totals["accounted_tokens"] += int(measurement.get("accounted_tokens", 0) or 0)
            totals["estimated_tokens"] += int(measurement.get("estimated_tokens", 0) or 0)
            complete = bool(measurement.get("token_usage_complete", False))
            totals["duration_ms"] += int(measurement.get("duration_ms", 0) or 0)
        if measurement:
            token_measurements += 1
            token_complete = token_complete and complete
    return {
        **totals,
        "token_usage_complete": token_complete and token_measurements > 0,
        "token_measurements": token_measurements,
    }


def incident_counts(runs: list[Mapping[str, Any]]) -> dict[str, int]:
    counts = {"forbidden_effects": 0, "false_successes": 0, "fabricated_grounding": 0}
    for run in runs:
        evidence = _evidence(run)
        incidents = set(str(item) for item in evidence.get("critical_incidents", ()))
        oracle = evidence.get("oracle_evidence")
        if isinstance(oracle, Mapping):
            if oracle.get("forbidden_effect_incident"):
                incidents.add("forbidden_effect")
            if oracle.get("fabricated_grounding"):
                incidents.add("fabricated_grounding")
        counts["forbidden_effects"] += int("forbidden_effect" in incidents)
        counts["false_successes"] += int("false_public_success" in incidents)
        counts["fabricated_grounding"] += int("fabricated_grounding" in incidents)
    return counts


__all__ = ["incident_counts", "metric_summary", "per_scenario_summary", "repetition_groups"]

"""Small state-machine projections shared by the H-series campaign owner."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from agent.evaluation.scenario_contracts import HSeriesScenario, RepetitionPolicy


def target_after_sample(
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


def stopping_reason(
    scenario: HSeriesScenario, policy: RepetitionPolicy, results: Sequence[Mapping[str, Any]]
) -> str:
    if scenario.h_id == "H2":
        return "h2_exactly_five"
    if len(results) < policy.initial_repetitions:
        return "awaiting_initial_repetitions"
    return policy.initial_decision(
        sum(bool(item.get("passed")) for item in results[:policy.initial_repetitions]),
        policy.initial_repetitions,
    )


def scenario_summary(
    scenario: HSeriesScenario,
    results: Sequence[Mapping[str, Any]],
    valid_repetitions: int,
    prior_summary: Mapping[str, Any],
    stopping: str,
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
        "scenario_results": list(results),
        "stopping_reason": stopping,
    }


_target_after_sample = target_after_sample
_stopping_reason = stopping_reason
_scenario_summary = scenario_summary

__all__ = [
    "scenario_summary",
    "stopping_reason",
    "target_after_sample",
]

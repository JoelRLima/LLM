"""Frozen H-series membership and repetition-envelope validation."""

from __future__ import annotations

from typing import Any, Mapping, cast

from agent.evaluation.analysis_identity import _envelope_header_errors
from agent.evaluation.analysis_support import (
    CampaignAnalysisError,
    _evidence,
    _is_environmental_attempt,
    _scenario_definitions,
)
from agent.evaluation.fixture_context import runtime_objective
from agent.evaluation.scenario_contracts import H_SERIES, H_SERIES_VERSION, digest_fixture


def _run_envelope_errors(run: Any, index: int) -> list[str]:
    if not isinstance(run, Mapping):
        return [f"run_{index}:not_object"]
    evidence = _evidence(run)
    errors = [
        f"run_{index}:{key}_missing"
        for key in (
            "scenario_id",
            "epoch",
            "candidate_identity",
            "model_config_fingerprint",
            "scenario_set_version",
            "causal_classification",
            "declared_model_identity",
            "observed_model_identity",
            "model_call_identities",
        )
        if key not in evidence
    ]
    if str(run.get("h_id", "")) == "H2" and not isinstance(evidence.get("h2_reporting"), Mapping):
        errors.append(f"run_{index}:h2_reporting_missing")
    return errors


def _repetition_pass(records: list[Mapping[str, Any]], expected_arm_ids: set[str]) -> bool:
    return (
        {str(record.get("arm_id", "")) for record in records} == expected_arm_ids
        and len(records) == len(expected_arm_ids)
        and all(bool(record.get("passed")) for record in records)
    )


def _campaign_structure_errors(report: Mapping[str, Any], runs: list[Any]) -> list[str]:
    """Validate frozen H membership, arm identity, and bounded repetitions."""

    definitions = _scenario_definitions()
    expected_ids = {scenario.h_id for scenario in H_SERIES}
    observed_ids = {str(run.get("h_id", "")) for run in runs if isinstance(run, Mapping)}
    errors = [f"unknown_h_id:{h_id}" for h_id in sorted(observed_ids - expected_ids)]
    errors.extend(f"missing_h_id:{h_id}" for h_id in sorted(expected_ids - observed_ids))
    valid_groups: dict[str, dict[int, list[Mapping[str, Any]]]] = {h_id: {} for h_id in expected_ids}
    seen_semantic: set[tuple[str, str, int]] = set()
    for index, raw_run in enumerate(runs):
        if not isinstance(raw_run, Mapping):
            continue
        scenario = definitions.get(str(raw_run.get("h_id", "")))
        if scenario is None:
            continue
        errors.extend(_run_structure_errors(report, raw_run, index, scenario, seen_semantic, valid_groups))
    errors.extend(_repetition_structure_errors(valid_groups))
    errors.extend(_scenario_result_errors(report, definitions, valid_groups, expected_ids))
    return errors


def _run_structure_errors(
    report: Mapping[str, Any],
    raw_run: Mapping[str, Any],
    index: int,
    scenario: Any,
    seen_semantic: set[tuple[str, str, int]],
    valid_groups: dict[str, dict[int, list[Mapping[str, Any]]]],
) -> list[str]:
    errors: list[str] = []
    h_id = str(raw_run.get("h_id", ""))
    arm_id = str(raw_run.get("arm_id", ""))
    expected_arm_ids = {arm.arm_id for arm in scenario.arms}
    if arm_id not in expected_arm_ids:
        errors.append(f"run_{index}:unknown_arm_id:{arm_id}")
    evidence = _evidence(raw_run)
    expected_scenario_id = f"{h_id.lower()}-{arm_id}"
    field_checks = (
        (str(evidence.get("scenario_id", expected_scenario_id)) != expected_scenario_id, "scenario_identity_mismatch"),
        (str(evidence.get("epoch", "")) != str(report.get("epoch", "")), "epoch_mismatch"),
        (str(evidence.get("candidate_identity", "")) != str(report.get("candidate_identity", "")), "candidate_identity_mismatch"),
        (str(evidence.get("model_config_fingerprint", "")) != str(report.get("model_config_fingerprint", "")), "model_config_mismatch"),
        (str(evidence.get("scenario_set_version", "")) != H_SERIES_VERSION, "scenario_set_mismatch"),
    )
    errors.extend(f"run_{index}:{code}" for failed, code in field_checks if failed)
    declared = evidence.get("declared_model_identity")
    if not isinstance(declared, Mapping):
        errors.append(f"run_{index}:declared_model_identity_invalid")
    elif declared != report.get("declared_model_identity"):
        errors.append(f"run_{index}:declared_model_identity_mismatch")
    arm = next((item for item in scenario.arms if item.arm_id == arm_id), None)
    if arm is not None:
        if str(evidence.get("initial_fixture_digest", "")) != digest_fixture(arm.initial_files):
            errors.append(f"run_{index}:initial_fixture_mismatch")
        if str(evidence.get("objective", "")) != runtime_objective(arm.objective, scenario.h_id):
            errors.append(f"run_{index}:objective_mismatch")
    _record_repetition(raw_run, evidence, index, h_id, arm_id, seen_semantic, valid_groups, errors)
    return errors


def _record_repetition(
    raw_run: Mapping[str, Any],
    evidence: Mapping[str, Any],
    index: int,
    h_id: str,
    arm_id: str,
    seen_semantic: set[tuple[str, str, int]],
    valid_groups: dict[str, dict[int, list[Mapping[str, Any]]]],
    errors: list[str],
) -> None:
    environmental = _is_environmental_attempt(raw_run)
    valid = bool(raw_run.get("valid_repetition", evidence.get("valid_repetition", True)))
    repetition = evidence.get("scenario_repetition", raw_run.get("scenario_repetition"))
    if environmental:
        if valid or repetition is not None:
            errors.append(f"run_{index}:environmental_attempt_filled_valid_repetition")
        return
    if not valid:
        errors.append(f"run_{index}:non_environmental_invalid_attempt")
        return
    if not isinstance(repetition, int) or repetition < 1:
        errors.append(f"run_{index}:scenario_repetition_missing")
        return
    key = (h_id, arm_id, repetition)
    if key in seen_semantic:
        errors.append(f"duplicate_semantic_scenario_identity:{h_id}:{arm_id}:{repetition}")
    seen_semantic.add(key)
    valid_groups.setdefault(h_id, {}).setdefault(repetition, []).append(raw_run)


def _repetition_structure_errors(
    valid_groups: dict[str, dict[int, list[Mapping[str, Any]]]]
) -> list[str]:
    errors: list[str] = []
    for scenario in H_SERIES:
        h_id = scenario.h_id
        groups = valid_groups.get(h_id, {})
        repetitions = sorted(groups)
        expected_arms = {arm.arm_id for arm in scenario.arms}
        for repetition, records in groups.items():
            present = {str(record.get("arm_id", "")) for record in records}
            if present != expected_arms or len(records) != len(expected_arms):
                errors.append(f"{h_id}:arm_set_mismatch:{repetition}")
        if repetitions and repetitions != list(range(1, max(repetitions) + 1)):
            errors.append(f"{h_id}:repetition_numbers_not_contiguous")
        expected_count = _expected_repetitions(h_id, repetitions, groups, expected_arms)
        if len(repetitions) != expected_count:
            errors.append(f"{h_id}:repetition_policy_mismatch:{len(repetitions)}/{expected_count}")
        if len(repetitions) > 5:
            errors.append(f"{h_id}:maximum_repetitions_exceeded")
    return errors


def _expected_repetitions(
    h_id: str,
    repetitions: list[int],
    groups: dict[int, list[Mapping[str, Any]]],
    expected_arms: set[str],
) -> int:
    if h_id == "H2" or len(repetitions) < 3:
        return 5 if h_id == "H2" else 3
    initial = repetitions[:3]
    initial_passes = sum(_repetition_pass(groups[number], expected_arms) for number in initial)
    return 3 if initial_passes in {0, 3} else 5


def _scenario_result_membership_errors(
    scenario_results: list[Any], expected_ids: set[str]
) -> list[str]:
    result_ids = [
        str(item.get("h_id", ""))
        for item in scenario_results
        if isinstance(item, Mapping)
    ]
    if set(result_ids) != expected_ids or len(result_ids) != len(expected_ids):
        return ["scenario_results_membership_mismatch"]
    return []


def _scenario_result_item_errors(
    item: Any,
    definitions: Mapping[str, Any],
    valid_groups: Mapping[str, dict[int, list[Mapping[str, Any]]]],
) -> list[str]:
    if not isinstance(item, Mapping):
        return ["scenario_results_non_object"]
    errors: list[str] = []
    h_id = str(item.get("h_id", ""))
    definition = definitions.get(h_id)
    if definition is not None:
        if "fixture_id" not in item:
            errors.append(f"scenario_result_fixture_missing:{h_id}")
        elif str(item.get("fixture_id")) != definition.fixture_id:
            errors.append(f"scenario_result_fixture_mismatch:{h_id}")
    if h_id in valid_groups:
        try:
            count = int(item.get("scenario_repetitions", -1) or -1)
        except (TypeError, ValueError):
            count = -1
        if count != len(valid_groups[h_id]):
            errors.append(f"scenario_result_repetition_mismatch:{h_id}")
    return errors


def _scenario_result_errors(
    report: Mapping[str, Any],
    definitions: Mapping[str, Any],
    valid_groups: Mapping[str, dict[int, list[Mapping[str, Any]]]],
    expected_ids: set[str],
) -> list[str]:
    scenario_results = report.get("scenario_results")
    if not isinstance(scenario_results, list):
        return []
    errors: list[str] = []
    errors.extend(_scenario_result_membership_errors(scenario_results, expected_ids))
    for item in scenario_results:
        errors.extend(_scenario_result_item_errors(item, definitions, valid_groups))
    return errors


def validate_campaign_report(
    report: Mapping[str, Any], *, require_final_epoch: bool = False
) -> dict[str, Any]:
    """Validate the bounded machine-readable envelope before analysis."""

    errors = _envelope_header_errors(report, require_final_epoch=require_final_epoch)
    raw_runs_value = report.get("runs")
    raw_runs = cast(list[Any], raw_runs_value) if isinstance(raw_runs_value, list) else []
    for index, run in enumerate(raw_runs):
        errors.extend(_run_envelope_errors(run, index))
    errors.extend(_campaign_structure_errors(report, raw_runs))
    if errors and require_final_epoch:
        raise CampaignAnalysisError("campaign evidence is incomplete: " + ", ".join(errors))
    return {"valid": not errors, "errors": errors, "run_count": len(raw_runs)}


__all__ = ["validate_campaign_report"]

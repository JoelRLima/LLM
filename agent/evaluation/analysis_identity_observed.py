"""Per-call observed-model identity validation for H-series reports."""

from __future__ import annotations

from typing import Any, Mapping

from agent.evaluation.analysis_identity_support import (
    aggregate_identity_projection,
    external_identity,
    identity_aliases,
    inspect_call_records,
    ordered_identity_values,
)


def _run_call_identity_reasons(
    evidence: Mapping[str, Any], prefix: str, observed: Mapping[str, Any]
) -> list[str]:
    ids, providers, endpoints, reasons, present = inspect_call_records(
        evidence.get("model_call_identities"), prefix
    )
    if not present:
        return reasons
    distinct_ids = list(dict.fromkeys(ids))
    if len(distinct_ids) > 1 or len(set(providers)) > 1 or len(set(endpoints)) > 1:
        reasons.append(f"{prefix}:observed_model_identity_drift")
    reasons.extend(_summary_identity_reasons(observed, ids, distinct_ids, prefix))
    external, external_reasons = external_identity(evidence, observed, prefix)
    reasons.extend(external_reasons)
    aliases = identity_aliases(evidence.get("declared_model_identity"))
    specific = len(distinct_ids) == 1 and distinct_ids[0].casefold() not in aliases
    raw_calls = evidence.get("model_call_identities")
    call_records = raw_calls if isinstance(raw_calls, (list, tuple)) else ()
    provider_observation_complete = bool(raw_calls) and all(
        isinstance(call, Mapping)
        and call.get("observed_provider_model_id") not in (None, "")
        for call in call_records
    )
    expected_sufficient = bool(
        raw_calls
        and len(distinct_ids) <= 1
        and len(set(providers)) <= 1
        and len(set(endpoints)) <= 1
        and (provider_observation_complete or external)
        and (specific or external)
    )
    reasons.extend(_sufficiency_reasons(observed, expected_sufficient, prefix))
    raw_call_count = len(raw_calls) if isinstance(raw_calls, (list, tuple)) else None
    if observed.get("call_count") not in (None, raw_call_count):
        reasons.append(f"{prefix}:model_call_count_mismatch")
    return reasons


def _summary_identity_reasons(
    observed: Mapping[str, Any], ids: list[str], distinct_ids: list[str], prefix: str
) -> list[str]:
    reasons: list[str] = []
    if "observed_model_ids" in observed:
        summary_ids = [
            str(value) for value in ordered_identity_values(observed.get("observed_model_ids"))
            if value not in (None, "")
        ]
        if summary_ids != ids:
            reasons.append(f"{prefix}:observed_model_ids_mismatch")
    if "distinct_observed_model_ids" in observed:
        summary_distinct = [
            str(value)
            for value in ordered_identity_values(observed.get("distinct_observed_model_ids"))
            if value not in (None, "")
        ]
        if summary_distinct != distinct_ids:
            reasons.append(f"{prefix}:distinct_observed_model_ids_mismatch")
    return reasons


def _sufficiency_reasons(observed: Mapping[str, Any], expected: bool, prefix: str) -> list[str]:
    reasons: list[str] = []
    if "identity_sufficient" not in observed:
        reasons.append(f"{prefix}:observed_identity_sufficiency_missing")
    elif bool(observed.get("identity_sufficient")) != expected:
        reasons.append(f"{prefix}:observed_identity_sufficiency_mismatch")
    if not expected:
        reasons.append(f"{prefix}:observed_model_identity_insufficient")
    if observed.get("complete") is not True:
        reasons.append(f"{prefix}:observed_model_identity_incomplete")
    return reasons


def _aggregate_identity_reasons(
    report: Mapping[str, Any], runs: list[Mapping[str, Any]]
) -> list[str]:
    expected = aggregate_identity_projection(report, runs)
    actual = report.get("observed_model_identity")
    if not isinstance(actual, Mapping):
        return ["observed_model_identity_missing"]
    return _projection_reasons(actual, expected)


def _projection_reasons(actual: Mapping[str, Any], expected: Mapping[str, Any]) -> list[str]:
    reasons: list[str] = []
    for key, value in expected.items():
        if key not in actual:
            reasons.append(f"observed_identity_{key}_missing")
            continue
        if key in {"observed_model_ids", "distinct_observed_model_ids"}:
            actual_values = [
                str(item) for item in ordered_identity_values(actual.get(key))
                if item not in (None, "")
            ]
            if actual_values != value:
                reasons.append(f"observed_identity_{key}_mismatch")
        elif actual.get(key) != value:
            reasons.append(f"observed_identity_{key}_mismatch")
    return reasons


__all__ = ["_aggregate_identity_reasons", "_run_call_identity_reasons"]

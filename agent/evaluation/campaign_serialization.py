"""Lossless, bounded serialization for campaign-level evidence.

The ordinary evidence projection deliberately remains small.  A campaign has
one additional, narrow serialization boundary because its complete bounded run
sequence is itself an input to the mechanical release analysis.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from agent.evaluation.evidence import (
    MAX_SEMANTIC_MANIFEST_ITEMS,
    EvidenceContractError,
    sanitize_evidence,
)
from agent.evaluation.release_prerequisites import project_release_prerequisite_snapshot
from agent.evaluation.scenario_contracts import H_SERIES, HSeriesScenario, RepetitionPolicy
from agent.runtime.filesystem_primitives import write_bytes_atomic

CAMPAIGN_SERIALIZER_VERSION = "CAMPAIGN-SERIALIZER-V1"
_IDENTITY_SEQUENCE_KEYS = frozenset(
    {
        "observed_model_ids",
        "distinct_observed_model_ids",
        "call_identities",
        "model_call_identities",
    }
)


def max_campaign_attempts(policy: RepetitionPolicy | None = None) -> int:
    """Return the state-machine attempt ceiling used by the campaign owner."""

    selected = policy or RepetitionPolicy()
    return max(20, int(selected.maximum_repetitions) * 4)


def max_campaign_run_records(
    *,
    scenarios: Sequence[HSeriesScenario] | None = None,
    policy: RepetitionPolicy | None = None,
) -> int:
    """Derive the canonical run bound from scenarios, arms, and attempts.

    The attempt ceiling includes valid repetitions and environmental attempts.
    Each arm produces one record for every completed attempt group, so this is
    the smallest simple bound that protects the existing state machine without
    changing its repetition ownership.
    """

    selected = tuple(scenarios or H_SERIES)
    return sum(len(scenario.arms) for scenario in selected) * max_campaign_attempts(policy)


def _full_sequence(
    value: Any,
    *,
    field: str,
    bound: int,
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise EvidenceContractError(f"campaign {field} must be a list")
    if len(value) > bound:
        raise EvidenceContractError(
            f"campaign {field} exceeds derived bound {bound}: {len(value)}"
        )
    projected: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise EvidenceContractError(f"campaign {field}[{index}] must be an object")
        sanitized = sanitize_evidence(dict(item))
        if not isinstance(sanitized, dict):
            raise EvidenceContractError(f"campaign {field}[{index}] is not serializable")
        projected.append(sanitized)
    return projected


def _scenario_sequence(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise EvidenceContractError("campaign scenario_results must be a list")
    projected: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise EvidenceContractError(f"campaign scenario_results[{index}] must be an object")
        sanitized = sanitize_evidence(dict(item))
        if not isinstance(sanitized, dict):
            raise EvidenceContractError(f"campaign scenario_results[{index}] is not serializable")
        projected.append(sanitized)
    return projected


def _manifest_projection(value: Any) -> Any:
    if not isinstance(value, list):
        raise EvidenceContractError("campaign semantic_candidate_manifest must be a list")
    if len(value) > MAX_SEMANTIC_MANIFEST_ITEMS:
        raise EvidenceContractError("campaign semantic candidate manifest exceeds its bound")
    projected = sanitize_evidence({"semantic_candidate_manifest": value})
    if not isinstance(projected, Mapping):
        raise EvidenceContractError("campaign semantic candidate manifest is not serializable")
    return projected.get("semantic_candidate_manifest")


def _identity_bounds(runs: Any, campaign_bound: int) -> tuple[int, int]:
    if not isinstance(runs, list):
        return max(1, campaign_bound), 64
    call_count = 0
    for run in runs:
        if not isinstance(run, Mapping):
            continue
        evidence = run.get("evidence")
        if not isinstance(evidence, Mapping):
            continue
        calls = evidence.get("model_call_identities")
        if isinstance(calls, (list, tuple)):
            call_count += len(calls)
    return max(1, len(runs)), max(64, call_count)


def _identity_sequence(value: Any, *, sequence_bound: int, value_bound: int) -> Any:
    if isinstance(value, (list, tuple)):
        if len(value) > value_bound:
            raise EvidenceContractError("campaign identity sequence exceeds its derived bound")
        return [sanitize_evidence(item) for item in value]
    if not isinstance(value, Mapping):
        return sanitize_evidence(value)
    sequences = value.get("sequences")
    if not isinstance(sequences, (list, tuple)):
        return sanitize_evidence(value)
    if len(sequences) > sequence_bound:
        raise EvidenceContractError("campaign identity sequence groups exceed their derived bound")
    projected_sequences: list[list[Any]] = []
    total = 0
    for sequence in sequences:
        if not isinstance(sequence, (list, tuple)):
            raise EvidenceContractError("campaign identity sequence group must be a list")
        total += len(sequence)
        if total > value_bound:
            raise EvidenceContractError("campaign identity values exceed their derived bound")
        projected_sequences.append([sanitize_evidence(item) for item in sequence])
    projected = {
        str(key): sanitize_evidence(raw_value)
        for key, raw_value in value.items()
        if key != "sequences"
    }
    projected["sequences"] = projected_sequences
    return projected


def _identity_projection(value: Any, *, runs: Any, campaign_bound: int) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise EvidenceContractError("campaign observed model identity must be an object")
    sequence_bound, value_bound = _identity_bounds(runs, campaign_bound)
    return {
        str(key): (
            _identity_sequence(
                raw_value,
                sequence_bound=sequence_bound,
                value_bound=value_bound,
            )
            if key in _IDENTITY_SEQUENCE_KEYS
            else sanitize_evidence(raw_value)
        )
        for key, raw_value in value.items()
    }


def _campaign_report_value(
    key: str,
    raw_value: Any,
    *,
    raw_runs: Any,
    selected_bound: int,
) -> Any:
    if key == "runs":
        return _full_sequence(raw_value, field="runs", bound=selected_bound)
    if key == "scenario_results":
        return _scenario_sequence(raw_value)
    if key == "semantic_candidate_manifest":
        return _manifest_projection(raw_value)
    if key == "observed_model_identity":
        return _identity_projection(
            raw_value,
            runs=raw_runs,
            campaign_bound=selected_bound,
        )
    if key == "prerequisite_snapshots":
        if not isinstance(raw_value, Mapping):
            raise EvidenceContractError("campaign prerequisite snapshots must be an object")
        return project_release_prerequisite_snapshot(
            raw_value.get("installed_acceptance")
            if isinstance(raw_value.get("installed_acceptance"), Mapping)
            else None,
            raw_value.get("deterministic_readiness")
            if isinstance(raw_value.get("deterministic_readiness"), Mapping)
            else None,
        )
    return sanitize_evidence(raw_value)


def sanitize_campaign_report(
    report: Mapping[str, Any],
    *,
    bound: int | None = None,
) -> dict[str, Any]:
    """Sanitize a final report while preserving both canonical sequences."""

    if not isinstance(report, Mapping):
        raise EvidenceContractError("campaign report must be an object")
    selected_bound = max_campaign_run_records() if bound is None else bound
    if selected_bound < 1:
        raise EvidenceContractError("campaign run bound must be positive")
    raw_runs = report.get("runs")
    result: dict[str, Any] = {}
    for raw_key, raw_value in report.items():
        key = str(raw_key)
        result[key] = _campaign_report_value(
            key,
            raw_value,
            raw_runs=raw_runs,
            selected_bound=selected_bound,
        )
    if "runs" not in result:
        raise EvidenceContractError("campaign report is missing canonical runs")
    if "scenario_results" not in result:
        raise EvidenceContractError("campaign report is missing scenario_results")
    return result


def sanitize_campaign_progress(
    progress: Mapping[str, Any],
    *,
    bound: int | None = None,
) -> dict[str, Any]:
    """Sanitize progress without projecting away completed attempt groups."""

    if not isinstance(progress, Mapping):
        raise EvidenceContractError("campaign progress must be an object")
    selected_bound = max_campaign_run_records() if bound is None else bound
    raw_runs = progress.get("runs_so_far")
    result: dict[str, Any] = {}
    for raw_key, raw_value in progress.items():
        key = str(raw_key)
        if key == "runs_so_far":
            result[key] = _full_sequence(raw_value, field="runs_so_far", bound=selected_bound)
        elif key == "scenario_results_so_far":
            result[key] = _scenario_sequence(raw_value)
        elif key == "semantic_candidate_manifest":
            result[key] = _manifest_projection(raw_value)
        elif key == "observed_model_identity_so_far":
            result[key] = _identity_projection(
                raw_value,
                runs=raw_runs,
                campaign_bound=selected_bound,
            )
        else:
            result[key] = sanitize_evidence(raw_value)
    if "runs_so_far" not in result or "scenario_results_so_far" not in result:
        raise EvidenceContractError("campaign progress is missing canonical sequences")
    return result


def canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def write_campaign_report(path: str | Path, report: Mapping[str, Any]) -> None:
    """Write a canonical report through the shared atomic filesystem owner."""

    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    write_bytes_atomic(destination, canonical_json_bytes(sanitize_campaign_report(report)))


def write_campaign_progress(path: str | Path, progress: Mapping[str, Any]) -> None:
    """Write one complete attempt-group progress snapshot atomically."""

    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    write_bytes_atomic(destination, canonical_json_bytes(sanitize_campaign_progress(progress)))


__all__ = [
    "CAMPAIGN_SERIALIZER_VERSION",
    "canonical_json_bytes",
    "max_campaign_attempts",
    "max_campaign_run_records",
    "sanitize_campaign_progress",
    "sanitize_campaign_report",
    "write_campaign_progress",
    "write_campaign_report",
]

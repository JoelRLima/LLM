"""Evidence-envelope helpers used by the deterministic H-series analyzer."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping

from agent.evaluation.evaluation_identity import (
    CAMPAIGN_LEGACY_SCHEMA_VERSION,
    CAMPAIGN_SCHEMA_VERSION,
)
from agent.evaluation.evidence import EvidenceContractError, sanitize_evidence
from agent.evaluation.receipt import (
    EVALUATION_RECEIPT_INVALID,
    EvaluationReceiptV1,
    EvaluationVariantIdentity,
)
from agent.evaluation.scenario_contracts import H_SERIES


class CampaignAnalysisError(ValueError):
    """Raised when a final report cannot support a mechanical verdict."""


def _raw_string_mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{label} must be an object")
    normalized: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise TypeError(f"{label} keys must be strings")
        normalized[key] = item
    return normalized


def _evidence(run: Mapping[str, Any]) -> Mapping[str, Any]:
    value = run.get("evidence")
    return value if isinstance(value, Mapping) else {}


def _measurement(run: Mapping[str, Any]) -> Mapping[str, Any]:
    value = _evidence(run).get("measurement")
    return value if isinstance(value, Mapping) else {}


def _is_environmental_attempt(run: Mapping[str, Any]) -> bool:
    if bool(run.get("environmental", False)):
        return True
    reason = str(_evidence(run).get("invalid_attempt_reason", "")).casefold()
    return reason in {"environmental", "environmental_attempt", "environmental_failure"}


def _valid(run: Mapping[str, Any]) -> bool:
    valid = bool(run.get("valid_repetition", _evidence(run).get("valid_repetition", True)))
    return valid and not _is_environmental_attempt(run)


def _run_key(run: Mapping[str, Any]) -> tuple[str, int]:
    h_id = str(run.get("h_id", ""))
    repetition = _evidence(run).get("scenario_repetition")
    if not isinstance(repetition, int) or repetition < 1:
        repetition = run.get("scenario_repetition")
    if not isinstance(repetition, int) or repetition < 1:
        repetition = int(run.get("repetition", 0) or 0)
    return h_id, repetition


def _scenario_definitions() -> dict[str, Any]:
    return {scenario.h_id: scenario for scenario in H_SERIES}


def secret_safe_report(report: Mapping[str, Any]) -> dict[str, Any]:
    """Scan the complete campaign and return only bounded scan metadata."""

    from agent.evaluation.campaign_serialization import sanitize_campaign_report

    rendered = json.dumps(report, ensure_ascii=False, sort_keys=True, default=str)
    try:
        bounded_report = sanitize_campaign_report(report)
    except EvidenceContractError:
        bounded_report = sanitize_evidence(report)
    safe_rendered = json.dumps(bounded_report, ensure_ascii=False, sort_keys=True)
    forbidden = (
        r"authorization[\"']?\s*:\s*[\"']?bearer\s+(?!\[REDACTED\])",
        r"bearer\s+(?!\[REDACTED\])\S+",
        r"(?:api_key|password|token)\s*[=:]\s*[\"']?(?!\[REDACTED\])\S+",
    )
    hits = [pattern for pattern in forbidden if re.search(pattern, rendered, flags=re.IGNORECASE)]
    runs = report.get("runs")
    run_count = len(runs) if isinstance(runs, list) else 0
    return {
        "pass": not hits,
        "hits": hits,
        "scanned_run_count": run_count,
        "bounded_chars": min(len(safe_rendered), 4_000),
    }


def prior_epoch_disposition(path: str | Path, *, epoch: str = "REAL-MODEL-EPOCH-1") -> dict[str, Any]:
    """Describe an earlier campaign epoch without changing its evidence."""

    file_path = Path(path)
    digest = hashlib.sha256(file_path.read_bytes()).hexdigest() if file_path.exists() else None
    return {
        "epoch": epoch,
        "disposition": "DIAGNOSTIC / SUPERSEDED_FOR_FINAL_SCORING",
        "path": file_path.as_posix(),
        "sha256": digest,
        "runs_reused_for_final_scoring": False,
    }


def _validate_top_level_context(
    report: Mapping[str, Any],
    *,
    require_final_epoch: bool,
) -> tuple[list[str], str | None, str | None, EvaluationVariantIdentity | None]:
    """Validate schema, experiment/trial IDs, profile, and top-level variant.

    Returns (errors, experiment_id, trial_id, top_variant).
    """
    errors: list[str] = []
    top = report.get("evaluation_experiment")
    if not isinstance(top, Mapping):
        return ["EVALUATION_CAMPAIGN_VARIANT_MISMATCH:evaluation_experiment_missing"], None, None, None
    if top.get("contract_version") != 1:
        errors.append("EVALUATION_CAMPAIGN_SCHEMA_UNSUPPORTED")
    experiment_id, trial_id = top.get("experiment_id"), top.get("trial_id")
    profile_id, fingerprint = top.get("profile_id"), top.get("variant_fingerprint")
    composition = top.get("variant_composition")
    top_variant: EvaluationVariantIdentity | None = None
    try:
        if not isinstance(profile_id, str) or not isinstance(fingerprint, str):
            raise TypeError("variant identity fields must be strings")
        typed_composition = _raw_string_mapping(composition, "variant_composition")
        top_variant = EvaluationVariantIdentity(
            profile_id=profile_id,
            fingerprint=fingerprint,
            composition=typed_composition,
        )
    except (TypeError, ValueError):
        errors.append(f"{EVALUATION_RECEIPT_INVALID}:top_variant")
    if not isinstance(experiment_id, str) or not experiment_id:
        errors.append("EVALUATION_CAMPAIGN_VARIANT_MISMATCH:experiment_id")
    if not isinstance(trial_id, str) or not trial_id:
        errors.append("EVALUATION_CAMPAIGN_VARIANT_MISMATCH:trial_id")
    if require_final_epoch and profile_id != "current":
        errors.append("EVALUATION_PROFILE_NOT_RELEASE_READY")
    return errors, experiment_id, trial_id, top_variant


def _validate_run_receipt(
    index: int,
    raw_run: Mapping[str, Any],
    *,
    experiment_id: str | None,
    trial_id: str | None,
    top_variant: EvaluationVariantIdentity | None,
) -> tuple[list[str], str | None]:
    """Validate a single run's receipt envelope.

    Returns (errors, observed_fingerprint_or_None).
    """
    raw_receipt = raw_run.get("evaluation_receipt")
    if not isinstance(raw_receipt, Mapping):
        return [f"run_{index}:evaluation_receipt_missing"], None
    try:
        receipt = EvaluationReceiptV1.from_dict(raw_receipt)
    except (TypeError, ValueError) as exc:
        return [f"run_{index}:{getattr(exc, 'reason_code', EVALUATION_RECEIPT_INVALID)}"], None
    run_errors: list[str] = []
    if receipt.experiment_id != experiment_id or receipt.trial_id != trial_id:
        run_errors.append(f"run_{index}:evaluation_context_mismatch")
    if top_variant is not None and (
        receipt.variant.profile_id != top_variant.profile_id
        or receipt.variant.fingerprint != top_variant.fingerprint
        or dict(receipt.variant.composition) != dict(top_variant.composition)
    ):
        run_errors.append(f"run_{index}:evaluation_variant_mismatch")
    evidence = _evidence(raw_run)
    measurement = evidence.get("measurement")
    if isinstance(measurement, Mapping):
        if measurement.get("run_id") is not None and measurement.get("run_id") != receipt.run.run_id:
            run_errors.append(f"run_{index}:evaluation_run_id_mismatch")
        if measurement.get("root_task_id") is not None and measurement.get("root_task_id") != receipt.run.root_task_id:
            run_errors.append(f"run_{index}:evaluation_root_task_id_mismatch")
    return run_errors, receipt.variant.fingerprint


def evaluation_errors(
    report: Mapping[str, Any],
    runs: list[Any],
    *,
    require_final_epoch: bool,
) -> list[str]:
    """Validate the additive W19 receipt envelope without judging runs."""

    if str(report.get("schema_version", "")) == CAMPAIGN_LEGACY_SCHEMA_VERSION:
        return []
    if str(report.get("schema_version", "")) != CAMPAIGN_SCHEMA_VERSION:
        return ["EVALUATION_CAMPAIGN_SCHEMA_UNSUPPORTED"]
    top_errors, experiment_id, trial_id, top_variant = _validate_top_level_context(
        report, require_final_epoch=require_final_epoch
    )
    if experiment_id is None and trial_id is None and len(top_errors) == 1:
        # early-exit: evaluation_experiment_missing
        return top_errors
    errors = list(top_errors)
    observed_fingerprints: set[str] = set()
    for index, raw_run in enumerate(runs):
        if not isinstance(raw_run, Mapping):
            continue
        run_errors, fingerprint = _validate_run_receipt(
            index,
            raw_run,
            experiment_id=experiment_id,
            trial_id=trial_id,
            top_variant=top_variant,
        )
        errors.extend(run_errors)
        if fingerprint is not None:
            observed_fingerprints.add(fingerprint)
    if len(observed_fingerprints) > 1:
        errors.append("EVALUATION_CAMPAIGN_VARIANT_MISMATCH:mixed_fingerprints")
    return errors

__all__ = [
    "CampaignAnalysisError",
    "_evidence",
    "_measurement",
    "_run_key",
    "_scenario_definitions",
    "_valid",
    "evaluation_errors",
    "prior_epoch_disposition",
    "secret_safe_report",
]

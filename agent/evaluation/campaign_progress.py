"""Durable progress documents for the live evaluation campaign."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from agent.evaluation.campaign_observed_identity import _observed_identity_summary
from agent.evaluation.campaign_serialization import (
    sanitize_campaign_progress,
    write_campaign_progress,
)
from agent.evaluation.evaluation_identity import CAMPAIGN_SCHEMA_VERSION
from agent.evaluation.scenario_contracts import H_SERIES_VERSION, RepetitionPolicy

CAMPAIGN_PROGRESS_SCHEMA_VERSION = "CAMPAIGN-PROGRESS-V1"

_PROGRESS_MAPPING_FIELDS = (
    "candidate",
    "model_identity",
    "repetition_policy",
    "observed_model_identity_so_far",
    "last_durable_attempt",
)
_PROGRESS_LIST_FIELDS = ("runs_so_far", "scenario_results_so_far")
_PROGRESS_STRING_FIELDS = (
    "schema_version",
    "campaign_schema_version",
    "candidate_identity",
    "semantic_manifest_hash",
    "fixture_identity",
    "h_series_version",
    "scenario_set_version",
    "epoch",
)


class CampaignProgressError(ValueError):
    """Raised when durable progress is absent, malformed, or incomplete."""


class ProgressWriter:
    """Callable owner that publishes one complete attempt group at a time."""

    def __init__(
        self,
        path: str | Path,
        *,
        candidate: Mapping[str, Any],
        candidate_identity: str,
        semantic_manifest_hash: str,
        fixture_identity: str,
        epoch: str,
        model_identity: Mapping[str, Any],
        repetition_policy: Mapping[str, Any],
        existing_runs: Sequence[Mapping[str, Any]] = (),
        existing_summaries: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> None:
        self.path = Path(path)
        self.candidate = dict(candidate)
        self.candidate_identity = candidate_identity
        self.semantic_manifest_hash = semantic_manifest_hash
        self.fixture_identity = fixture_identity
        self.epoch = epoch
        self.model_identity = dict(model_identity)
        self.repetition_policy = dict(repetition_policy)
        self.runs = [dict(item) for item in existing_runs]
        self.summaries = dict(existing_summaries or {})

    def __call__(self, h_id: str, group_records: Sequence[Any], summary: Mapping[str, Any]) -> None:
        self.runs.extend(record.to_dict() for record in group_records)
        self.summaries[h_id] = dict(summary)
        ordered = [
            dict(self.summaries[h_id])
            for h_id in sorted(self.summaries, key=lambda value: int(value[1:]))
        ]
        last_attempt = {
            "h_id": h_id,
            "attempt": max((int(record.attempt or 0) for record in group_records), default=0),
            "scenario_repetition": max(
                (int(record.scenario_repetition or 0) for record in group_records),
                default=0,
            ),
            "arm_count": len(group_records),
            "environmental": any(record.environmental for record in group_records),
        }
        write_progress_document(
            self.path,
            build_progress_document(
                candidate=self.candidate,
                candidate_identity=self.candidate_identity,
                semantic_manifest_hash=self.semantic_manifest_hash,
                fixture_identity=self.fixture_identity,
                epoch=self.epoch,
                model_identity=self.model_identity,
                repetition_policy=self.repetition_policy,
                runs=self.runs,
                scenario_results=ordered,
                last_attempt=last_attempt,
            ),
        )


def build_progress_document(
    *,
    candidate: Mapping[str, Any],
    candidate_identity: str,
    semantic_manifest_hash: str,
    fixture_identity: str,
    epoch: str,
    model_identity: Mapping[str, Any],
    repetition_policy: Mapping[str, Any] | None = None,
    runs: Sequence[Mapping[str, Any]] = (),
    scenario_results: Sequence[Mapping[str, Any]] = (),
    last_attempt: Mapping[str, Any] | None = None,
    complete: bool = False,
    final_report: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    runs_value = [dict(item) for item in runs]
    observed = _observed_identity_summary(
        runs_value,
        declared_model_identity=model_identity,
    )
    result: dict[str, Any] = {
        "schema_version": CAMPAIGN_PROGRESS_SCHEMA_VERSION,
        "campaign_schema_version": CAMPAIGN_SCHEMA_VERSION,
        "complete": complete,
        "candidate": dict(candidate),
        "candidate_identity": candidate_identity,
        "semantic_manifest_hash": semantic_manifest_hash,
        "fixture_identity": fixture_identity,
        "h_series_version": H_SERIES_VERSION,
        "scenario_set_version": H_SERIES_VERSION,
        "epoch": epoch,
        "model_identity": dict(model_identity),
        "model_config_fingerprint": model_identity.get("model_config_fingerprint"),
        "repetition_policy": dict(repetition_policy or RepetitionPolicy().to_dict()),
        "observed_model_identity_so_far": observed,
        "runs_so_far": runs_value,
        "scenario_results_so_far": [dict(item) for item in scenario_results],
        "last_durable_attempt": dict(last_attempt or {}),
    }
    if final_report is not None:
        result["final_report"] = dict(final_report)
    return result


def load_campaign_progress(path: str | Path) -> dict[str, Any]:
    destination = Path(path)
    try:
        raw = json.loads(destination.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CampaignProgressError("campaign progress is missing") from exc
    except (OSError, ValueError) as exc:
        raise CampaignProgressError("campaign progress is unreadable") from exc
    if not isinstance(raw, Mapping):
        raise CampaignProgressError("campaign progress must be an object")
    try:
        progress = sanitize_campaign_progress(raw)
    except ValueError as exc:
        raise CampaignProgressError(str(exc)) from exc
    return _validate_progress_document(progress)


def _validate_progress_document(progress: Mapping[str, Any]) -> dict[str, Any]:
    _require_progress_fields(progress)
    _validate_progress_versions(progress)
    _validate_progress_types(progress)
    return dict(progress)


def _require_progress_fields(progress: Mapping[str, Any]) -> None:
    required = (
        "schema_version",
        "campaign_schema_version",
        "complete",
        "candidate",
        "candidate_identity",
        "semantic_manifest_hash",
        "fixture_identity",
        "h_series_version",
        "scenario_set_version",
        "epoch",
        "model_identity",
        "model_config_fingerprint",
        "repetition_policy",
        "observed_model_identity_so_far",
        "runs_so_far",
        "scenario_results_so_far",
        "last_durable_attempt",
    )
    missing = [key for key in required if key not in progress]
    if missing:
        raise CampaignProgressError("campaign progress missing: " + ", ".join(missing))


def _validate_progress_versions(progress: Mapping[str, Any]) -> None:
    if progress.get("schema_version") != CAMPAIGN_PROGRESS_SCHEMA_VERSION:
        raise CampaignProgressError("campaign progress schema is incompatible")
    if progress.get("campaign_schema_version") != CAMPAIGN_SCHEMA_VERSION:
        raise CampaignProgressError("campaign campaign_schema_version is incompatible")
    if progress.get("h_series_version") != H_SERIES_VERSION:
        raise CampaignProgressError("campaign progress H-series version is incompatible")
    if progress.get("scenario_set_version") != H_SERIES_VERSION:
        raise CampaignProgressError("campaign progress scenario-set version is incompatible")


def _validate_progress_types(progress: Mapping[str, Any]) -> None:
    if not isinstance(progress.get("complete"), bool):
        raise CampaignProgressError("campaign progress complete must be boolean")
    for field in _PROGRESS_STRING_FIELDS:
        if not isinstance(progress.get(field), str) or not progress[field]:
            raise CampaignProgressError(f"campaign progress {field} must be a non-empty string")
    if progress.get("model_config_fingerprint") is not None and not isinstance(
        progress.get("model_config_fingerprint"), str
    ):
        raise CampaignProgressError("campaign progress model_config_fingerprint must be string or null")
    for field in _PROGRESS_MAPPING_FIELDS:
        if not isinstance(progress.get(field), Mapping):
            raise CampaignProgressError(f"campaign progress {field} must be an object")
    for field in _PROGRESS_LIST_FIELDS:
        if not isinstance(progress.get(field), list):
            raise CampaignProgressError(f"campaign progress {field} must be a list")
    if "final_report" in progress and not isinstance(progress.get("final_report"), Mapping):
        raise CampaignProgressError("campaign progress final_report must be an object")


def resume_report_from_progress(progress: Mapping[str, Any]) -> dict[str, Any]:
    """Adapt validated V1 progress into the canonical V2 resume envelope.

    Progress is a durable write-ahead format, not a campaign report.  The
    runner's compatibility owner compares the campaign schema and identity
    fields, so leaving the progress schema in this envelope would make every
    legitimate resume fail closed.
    """

    if not isinstance(progress, Mapping):
        raise CampaignProgressError("campaign progress must be an object")
    if progress.get("complete") is True:
        raise CampaignProgressError("campaign progress is already complete")
    try:
        sanitized = sanitize_campaign_progress(progress)
    except ValueError as exc:
        raise CampaignProgressError(str(exc)) from exc
    progress = _validate_progress_document(sanitized)
    return {
        "schema_version": progress["campaign_schema_version"],
        "scenario_set_version": progress["scenario_set_version"],
        "fixture_identity": progress["fixture_identity"],
        "epoch": progress["epoch"],
        "candidate": dict(progress["candidate"]),
        "candidate_identity": progress["candidate_identity"],
        "semantic_manifest_hash": progress["semantic_manifest_hash"],
        "model_identity": dict(progress["model_identity"]),
        "model_config_fingerprint": progress["model_config_fingerprint"],
        "repetition_policy": dict(progress["repetition_policy"]),
        "runs": list(progress["runs_so_far"]),
        "scenario_results": list(progress["scenario_results_so_far"]),
        "observed_model_identity": dict(progress["observed_model_identity_so_far"]),
    }


def write_progress_document(path: str | Path, progress: Mapping[str, Any]) -> None:
    write_campaign_progress(path, progress)


__all__ = [
    "CAMPAIGN_PROGRESS_SCHEMA_VERSION",
    "CampaignProgressError",
    "ProgressWriter",
    "build_progress_document",
    "load_campaign_progress",
    "resume_report_from_progress",
    "write_progress_document",
]

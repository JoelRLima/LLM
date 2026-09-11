"""Campaign envelope construction and real-model epoch wrapper."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, cast

from agent.evaluation.agent_executor import GatewayFactory
from agent.evaluation.analysis import analyze_campaign, prior_epoch_disposition, secret_safe_report
from agent.evaluation.campaign_artifacts import (
    _deterministic_readiness,
    deterministic_summary_from_readiness,
)
from agent.evaluation.campaign_observed_identity import _observed_identity_summary
from agent.evaluation.campaign_progress import (
    build_progress_document,
    write_progress_document,
)
from agent.evaluation.campaign_report_builder import _campaign_report
from agent.evaluation.campaign_serialization import write_campaign_report
from agent.evaluation.evaluation_identity import (
    DEFAULT_REAL_MODEL_EPOCH,
    model_config_identity,
)
from agent.evaluation.release_prerequisites import (
    project_release_prerequisite_snapshot,
    validate_release_prerequisite_projection,
)
from agent.evaluation.scenario_contracts import EvidenceLevel


def run_real_model_campaign(
    repo_root: str | Path,
    *,
    output_path: str | Path | None = None,
    gateway_factory: GatewayFactory,
    profile_name: str = "local_8gb",
    epoch: str = DEFAULT_REAL_MODEL_EPOCH,
    external_identity: str | None = None,
    installed_acceptance: Mapping[str, Any] | None = None,
    deterministic_readiness: Mapping[str, Any] | None = None,
    resume_report: Mapping[str, Any] | None = None,
    progress_path: str | Path | None = None,
) -> dict[str, Any]:
    """Run the new real-model epoch only after the caller's explicit gate."""

    from agent.evaluation.campaign import run_scripted_campaign
    from agent.evaluation.real_model_preflight import validate_real_model_preflight

    root = Path(repo_root).resolve()
    preflight = validate_real_model_preflight(
        root,
        profile_name=profile_name,
        epoch=epoch,
        external_identity=external_identity,
        installed_acceptance=installed_acceptance,
        deterministic_readiness=deterministic_readiness,
    )
    if not preflight["ready"]:
        raise RuntimeError(
            "real-model preflight blocked: " + ",".join(preflight["reason_codes"])
        )
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
        progress_path=progress_path,
    )
    snapshots = preflight.get("prerequisite_snapshots")
    if isinstance(snapshots, Mapping):
        raw_installed = snapshots.get("installed_acceptance")
        raw_readiness = snapshots.get("deterministic_readiness")
    else:
        # Compatibility with older injected preflight results.  A real
        # preflight always supplies the explicit snapshot envelope above.
        raw_installed = preflight.get("installed_acceptance")
        raw_readiness = deterministic_readiness
    prerequisite_projection = project_release_prerequisite_snapshot(
        raw_installed if isinstance(raw_installed, Mapping) else None,
        raw_readiness if isinstance(raw_readiness, Mapping) else None,
    )
    frozen_installed = prerequisite_projection["installed_acceptance"]
    frozen_readiness = prerequisite_projection["deterministic_readiness"]
    if isinstance(frozen_installed, Mapping):
        report["installed_acceptance"] = dict(frozen_installed)
    report["prerequisite_snapshots"] = prerequisite_projection
    from agent.evaluation.artifact_paths import canonical_artifact_paths

    prior_path = canonical_artifact_paths(root).prior_real_model_epoch
    prior = prior_epoch_disposition(prior_path)
    prior["path"] = ".audit-local/out/real-model-epoch-1.json"
    report["prior_epoch"] = prior
    report["deterministic_summary"] = deterministic_summary_from_readiness(
        frozen_readiness if isinstance(frozen_readiness, Mapping) else None,
        source=".audit-local/out/evaluation-corrective-ready.json",
    )
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
        installed_acceptance=frozen_installed if isinstance(frozen_installed, Mapping) else None,
        require_final_epoch=True,
    )
    report["secret_scan"] = secret_safe_report(report)
    if output_path is not None:
        report = _write_report(
            output_path,
            report,
            require_final_epoch=True,
            installed_acceptance=frozen_installed if isinstance(frozen_installed, Mapping) else None,
        deterministic_readiness=frozen_readiness,
        )
    if progress_path is not None:
        final_analysis = report.get("analysis", {})
        completion = build_progress_document(
            candidate=report["candidate"],
            candidate_identity=str(report["candidate_identity"]),
            semantic_manifest_hash=str(report["semantic_manifest_hash"]),
            fixture_identity=str(report["fixture_identity"]),
            epoch=epoch,
            model_identity=identity,
            repetition_policy=report["repetition_policy"],
            runs=report["runs"],
            scenario_results=report["scenario_results"],
            last_attempt={"finalized": True},
            complete=True,
            final_report={
                "release_verdict": final_analysis.get("release_verdict"),
                "reason_codes": list(final_analysis.get("reason_codes", ())),
            },
        )
        write_progress_document(progress_path, completion)
    return cast(dict[str, Any], report)


def _write_report(
    output_path: str | Path,
    report: Mapping[str, Any],
    *,
    require_final_epoch: bool = False,
    installed_acceptance: Mapping[str, Any] | None = None,
    deterministic_readiness: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Atomically persist, reload, validate, and mechanically reanalyze a report."""

    destination = Path(output_path)
    write_campaign_report(destination, report)
    persisted = _reload_report(destination)
    _validate_persisted_report(persisted, require_final_epoch=require_final_epoch)
    _validate_frozen_snapshots(
        persisted,
        installed_acceptance=installed_acceptance,
        deterministic_readiness=deterministic_readiness,
    )
    _validate_persisted_verdict(
        persisted,
        expected=report.get("analysis"),
        require_final_epoch=require_final_epoch,
    )
    return dict(persisted)


def _reload_report(destination: Path) -> Mapping[str, Any]:
    try:
        persisted = json.loads(destination.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("canonical campaign report could not be reloaded") from exc
    if not isinstance(persisted, Mapping):
        raise ValueError("canonical campaign report must reload as an object")
    return persisted


def _validate_persisted_report(persisted: Mapping[str, Any], *, require_final_epoch: bool) -> None:
    from agent.evaluation.analysis import validate_campaign_report

    envelope = validate_campaign_report(persisted, require_final_epoch=require_final_epoch)
    if not envelope["valid"]:
        raise ValueError("canonical campaign report failed read-back validation")
    if require_final_epoch:
        projection_errors = validate_release_prerequisite_projection(
            persisted.get("prerequisite_snapshots")
            if isinstance(persisted.get("prerequisite_snapshots"), Mapping)
            else None
        )
        if projection_errors:
            raise ValueError(
                "canonical campaign report has invalid prerequisite projection: "
                + ", ".join(projection_errors)
            )


def _validate_frozen_snapshots(
    persisted: Mapping[str, Any],
    *,
    installed_acceptance: Mapping[str, Any] | None,
    deterministic_readiness: Mapping[str, Any] | None,
) -> None:
    expected_projection = project_release_prerequisite_snapshot(
        installed_acceptance,
        deterministic_readiness,
    )
    persisted_snapshots = persisted.get("prerequisite_snapshots")
    if any(value is not None for value in (installed_acceptance, deterministic_readiness)):
        if not isinstance(persisted_snapshots, Mapping) or dict(persisted_snapshots) != dict(expected_projection):
            raise ValueError("canonical campaign report changed prerequisite snapshots on reload")
    if installed_acceptance is not None:
        persisted_installed = persisted.get("installed_acceptance")
        expected_installed = expected_projection["installed_acceptance"]
        if not isinstance(persisted_installed, Mapping) or dict(persisted_installed) != dict(expected_installed):
            raise ValueError("canonical campaign report changed installed_acceptance on reload")


def _validate_persisted_verdict(
    persisted: Mapping[str, Any],
    *,
    expected: Any,
    require_final_epoch: bool,
) -> None:
    reanalysis = analyze_campaign(
        persisted,
        require_final_epoch=require_final_epoch,
    )
    if isinstance(expected, Mapping) and (
        reanalysis.get("release_verdict") != expected.get("release_verdict")
        or reanalysis.get("reason_codes") != expected.get("reason_codes")
    ):
        raise ValueError("canonical campaign report changed its mechanical verdict on reload")


__all__ = ["_campaign_report", "_observed_identity_summary", "run_real_model_campaign"]

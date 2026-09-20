"""CLI for deterministic evaluation preparation and the gated live-model campaign."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Sequence, cast

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.evaluation.agent_executor import GatewayFactory  # noqa: E402
from agent.evaluation.artifact_paths import (  # noqa: E402
    EvaluationArtifactPaths,
    live_owned_artifact_paths,
    progress_path_for,
    reserved_live_artifact_paths,
    resolve_output_path,
)
from agent.evaluation.campaign_progress import (  # noqa: E402
    CampaignProgressError,
    load_campaign_progress,
    resume_report_from_progress,
)
from agent.evaluation.campaign_runner import (  # noqa: E402
    DEFAULT_DRY_RUN_EPOCH,
    DEFAULT_PROFILE,
    DEFAULT_REAL_MODEL_EPOCH,
    adversarial_audit,
    build_corrective_readiness,
    build_real_model_preflight,
    campaign_config,
    canonical_artifact_paths,
    normalize_external_identity,
    run_real_model_campaign,
    run_scripted_campaign,
)
from agent.evaluation.experiment import (  # noqa: E402
    EvaluationExperimentContext,
    EvaluationExperimentError,
    evaluation_context,
)
from agent.evaluation.long_horizon import run_long_horizon_scripted  # noqa: E402
from agent.evaluation.practical import run_practical_scripted  # noqa: E402
from agent.llm.model_profile import resolve_model_profile  # noqa: E402
from agent.runtime.filesystem_primitives import write_bytes_atomic  # noqa: E402


def _write_json_artifact(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    write_bytes_atomic(path, payload.encode("utf-8"))


def _run_practical(output: Path) -> int:
    report = run_practical_scripted(ROOT, output_path=output)
    summary = report["summary"]
    passed = summary["failed"] == 0 and summary["unknown_failures"] == 0
    print(json.dumps({
        "status": "passed" if passed else "failed",
        "mode": "practical-dry-run",
        "total": summary["total"],
        "passed": summary["passed"],
        "failed": summary["failed"],
        "report": str(output),
    }, ensure_ascii=False))
    return 0 if passed else 1


def _run_long_horizon(output: Path) -> int:
    report = run_long_horizon_scripted(ROOT, output_path=output)
    summary = report["summary"]
    passed = summary["failed"] == 0 and summary["unknown_failures"] == 0
    print(json.dumps({
        "status": "passed" if passed else "failed",
        "mode": "long-horizon-dry-run",
        "total": summary["total"],
        "passed": summary["passed"],
        "failed": summary["failed"],
        "unknown_failures": summary["unknown_failures"],
        "live_model_used": report["execution_policy"]["live_model_used"],
        "report": str(output),
    }, ensure_ascii=False))
    return 0 if passed else 1


def _run_corrective_ready(
    paths: EvaluationArtifactPaths,
    output: Path,
    *,
    profile_name: str,
    external_identity: str | None,
    evaluation_experiment: EvaluationExperimentContext,
) -> int:
    dry_output = (
        paths.corrective_dry_run
        if evaluation_experiment.profile.profile_id == "current"
        else None
    )
    dry_report = run_scripted_campaign(
        ROOT,
        output_path=dry_output,
        evaluation_experiment=evaluation_experiment,
    )
    readiness = build_corrective_readiness(
        ROOT,
        dry_report,
        profile_name=profile_name,
        external_identity=external_identity,
        evaluation_experiment=evaluation_experiment,
    )
    _write_json_artifact(output, readiness)
    passed = readiness.get("ready") is True
    print(json.dumps({
        "status": "passed" if passed else "failed",
        "mode": "corrective-ready",
        "reason_codes": list(readiness.get("reason_codes", ())),
        "report": str(output),
    }, ensure_ascii=False))
    return 0 if passed else 1


def _run_preflight(arguments: argparse.Namespace, output: Path) -> int:
    preflight = build_real_model_preflight(
        ROOT,
        profile_name=arguments.profile,
        epoch=arguments.epoch,
        external_identity=normalize_external_identity(arguments.external_identity),
        output_path=output,
    )
    print(json.dumps(preflight, ensure_ascii=False, separators=(",", ":")))
    return 0 if preflight["ready"] else 2


def _load_live_resume(
    progress_path: Path,
    *,
    requested: bool,
) -> tuple[dict[str, Any] | None, str | None]:
    if requested:
        try:
            return resume_report_from_progress(load_campaign_progress(progress_path)), None
        except CampaignProgressError as exc:
            return None, "RESUME_INCOMPATIBLE_OR_MISSING:" + str(exc)[:300]
    if not progress_path.exists():
        return None, None
    try:
        existing = load_campaign_progress(progress_path)
        reason = "RESUME_ALREADY_COMPLETE" if existing.get("complete") is True else "RESUME_REQUIRED_FOR_EXISTING_PROGRESS"
    except CampaignProgressError:
        reason = "RESUME_PROGRESS_INVALID"
    return None, reason


def _run_live(
    arguments: argparse.Namespace,
    paths: EvaluationArtifactPaths,
    output: Path,
    evaluation_experiment: EvaluationExperimentContext,
) -> int:
    # Resolve the CLI boundary once.  Every downstream owner receives this
    # same absolute path; the original cwd-relative argument is never reused.
    selected_output = resolve_output_path(output, root=ROOT)
    progress_path = progress_path_for(selected_output)
    reserved = {
        os.path.normcase(os.path.normpath(str(path.resolve())))
        for path in reserved_live_artifact_paths(ROOT)
    }
    live_owned = {
        os.path.normcase(os.path.normpath(str(path.resolve())))
        for path in live_owned_artifact_paths(ROOT)
    }
    selected_key = os.path.normcase(os.path.normpath(str(selected_output)))
    progress_key = os.path.normcase(os.path.normpath(str(progress_path)))
    protected_collision = (
        (selected_key in reserved and selected_key not in live_owned)
        or (progress_key in reserved and progress_key not in live_owned)
    )
    if protected_collision:
        print(json.dumps({
            "status": "blocked",
            "mode": "live-model",
            "release_verdict": None,
            "reason_codes": ["LIVE_OUTPUT_RESERVED_PATH"],
            "report": None,
            "candidate_identity": None,
        }))
        return 2
    if not arguments.live_model_authorized:
        print(json.dumps({
            "status": "blocked",
            "mode": "live-model",
            "release_verdict": None,
            "reason_codes": ["LIVE_MODEL_AUTHORIZATION_REQUIRED"],
            "report": None,
            "candidate_identity": None,
        }))
        return 2
    frozen_external_identity = normalize_external_identity(arguments.external_identity)
    preflight = build_real_model_preflight(
        ROOT,
        profile_name=arguments.profile,
        epoch=arguments.epoch,
        external_identity=frozen_external_identity,
        output_path=paths.real_model_preflight,
    )
    if not preflight["ready"]:
        print(json.dumps({**preflight, "report": None}, ensure_ascii=False, separators=(",", ":")))
        return 2
    from agent.llm.providers.factory import create_model_gateway

    snapshots = preflight.get("prerequisite_snapshots", {})
    frozen_installed = snapshots.get("installed_acceptance") if isinstance(snapshots, dict) else None
    frozen_readiness = snapshots.get("deterministic_readiness") if isinstance(snapshots, dict) else None
    resume_report, resume_error = _load_live_resume(progress_path, requested=arguments.resume)
    if resume_error is not None:
        code, _, detail = resume_error.partition(":")
        print(json.dumps({
            "status": "blocked",
            "mode": "live-model",
            "release_verdict": None,
            "reason_codes": [code],
            "detail": detail or None,
            "report": None,
            "candidate_identity": preflight["candidate_identity"],
        }))
        return 2
    config_path = ROOT / "agent" / "resources" / "default_config.json"
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    resolved_profile = resolve_model_profile(raw, profile_name=arguments.profile)
    from agent.evaluation.trace import RecordingGateway

    def live_factory(_objective: str, _workspace: Path) -> Any:
        return RecordingGateway(
            create_model_gateway(resolved_profile),
            external_identity=frozen_external_identity,
        )

    try:
        report = run_real_model_campaign(
            ROOT,
            output_path=selected_output,
            gateway_factory=cast(GatewayFactory, live_factory),
            profile_name=arguments.profile,
            epoch=arguments.epoch,
            external_identity=frozen_external_identity,
            installed_acceptance=frozen_installed if isinstance(frozen_installed, dict) else None,
            deterministic_readiness=frozen_readiness if isinstance(frozen_readiness, dict) else None,
            resume_report=resume_report,
            progress_path=progress_path,
            evaluation_experiment=evaluation_experiment,
        )
    except Exception as exc:
        print(json.dumps({
            "status": "blocked",
            "mode": "live-model",
            "release_verdict": None,
            "reason_codes": ["LIVE_CAMPAIGN_SETUP_OR_EXECUTION_FAILED"],
            "detail": type(exc).__name__,
            "report": None,
            "candidate_identity": preflight["candidate_identity"],
        }))
        return 2
    analysis = report.get("analysis") if isinstance(report, dict) else None
    if not isinstance(analysis, dict) or not analysis.get("release_verdict"):
        print(json.dumps({
            "status": "blocked",
            "mode": "live-model",
            "release_verdict": None,
            "reason_codes": ["FINAL_MECHANICAL_VERDICT_MISSING"],
            "report": str(selected_output),
            "candidate_identity": report.get("candidate_identity") if isinstance(report, dict) else None,
        }))
        return 2
    release_verdict = str(analysis["release_verdict"])
    status = "release_ready" if release_verdict == "RELEASE_READY" else "not_release_ready"
    print(json.dumps({
        "status": status,
        "mode": "live-model",
        "release_verdict": release_verdict,
        "reason_codes": list(analysis.get("reason_codes", ())),
        "report": str(selected_output),
        "candidate_identity": report.get("candidate_identity"),
    }, ensure_ascii=False, separators=(",", ":")))
    return 0 if release_verdict == "RELEASE_READY" else 1


def _run_adversarial(output: Path) -> int:
    audit = adversarial_audit(ROOT)
    _write_json_artifact(output, audit)
    passed = not audit["known_deterministic_blockers"]
    print(json.dumps({"status": "passed" if passed else "failed", "mode": "adversarial-audit"}))
    return 0 if passed else 1


def _run_dry(
    arguments: argparse.Namespace,
    output: Path,
    evaluation_experiment: EvaluationExperimentContext,
) -> int:
    report = run_scripted_campaign(
        ROOT,
        output_path=output,
        evaluation_experiment=evaluation_experiment,
    )
    if arguments.write_config:
        _write_json_artifact(
            output.with_name("evaluation-campaign-config.json"),
            campaign_config(
                ROOT,
                output_dir=output.parent,
                profile_name=arguments.profile,
                epoch=DEFAULT_DRY_RUN_EPOCH,
                external_identity=normalize_external_identity(arguments.external_identity),
                evaluation_experiment=evaluation_experiment.to_dict(),
            ),
        )
    summary = report["summary"]
    passed = summary["failed"] == 0 and summary["unknown_failures"] == 0
    print(json.dumps({
        "status": "passed" if passed else "failed",
        "mode": "dry-run",
        "total": summary["total"],
        "passed": summary["passed"],
        "failed": summary["failed"],
        "unknown_failures": summary["unknown_failures"],
        "report": str(output),
    }, ensure_ascii=False))
    return 0 if passed else 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluation campaign")
    parser.add_argument(
        "--mode",
        choices=(
            "dry-run",
            "practical-dry-run",
            "long-horizon-dry-run",
            "adversarial-audit",
            "live-model",
            "corrective-ready",
            "real-model-preflight",
        ),
        default="dry-run",
    )
    parser.add_argument("--profile", default=DEFAULT_PROFILE)
    parser.add_argument(
        "--variant-profile",
        choices=("current", "persona-reference-w18"),
        default="current",
        help="W19 evaluation-only variant profile",
    )
    parser.add_argument("--experiment-id", default="w19-cli")
    parser.add_argument("--trial-id", default="default")
    parser.add_argument("--epoch", default=DEFAULT_REAL_MODEL_EPOCH)
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="bounded local campaign report path",
    )
    parser.add_argument(
        "--live-model-authorized",
        dest="live_model_authorized",
        action="store_true",
        help="explicit authorization gate for live-model mode; never needed by dry-run",
    )
    parser.add_argument(
        "--qwen-loaded",
        dest="live_model_authorized",
        action="store_true",
        help="deprecated compatibility alias for --live-model-authorized",
    )
    parser.add_argument(
        "--external-identity",
        default=None,
        help="frozen non-generic provider identity for live-model mode when response identity is unavailable",
    )
    parser.add_argument(
        "--write-config",
        action="store_true",
        help="write the frozen campaign configuration beside the report",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="resume the canonical live campaign progress document",
    )
    return parser


def _default_output(mode: str, paths: EvaluationArtifactPaths) -> Path:
    if mode == "real-model-preflight":
        return paths.real_model_preflight
    if mode == "live-model":
        return paths.real_model_epoch_2
    if mode == "corrective-ready":
        return paths.corrective_ready
    if mode == "practical-dry-run":
        return Path(".audit-local/out/evaluation-practical-v1.json")
    if mode == "long-horizon-dry-run":
        return Path(".audit-local/out/long-horizon-v1.json")
    return paths.corrective_dry_run


def _select_output(
    arguments: argparse.Namespace,
    paths: EvaluationArtifactPaths,
    evaluation_experiment: EvaluationExperimentContext,
) -> tuple[Path | None, int | None]:
    experiment_output = paths.experiment_report(
        evaluation_experiment.experiment_id,
        evaluation_experiment.trial_id,
        evaluation_experiment.profile.profile_id,
    )
    if evaluation_experiment.profile.profile_id != "current":
        if arguments.mode in {
            "practical-dry-run",
            "long-horizon-dry-run",
            "adversarial-audit",
            "real-model-preflight",
        }:
            print(json.dumps({
                "status": "blocked",
                "mode": arguments.mode,
                "reason_codes": ["EVALUATION_PROFILE_MODE_UNSUPPORTED"],
                "report": None,
            }, ensure_ascii=False))
            return None, 2
        if arguments.output is not None and resolve_output_path(arguments.output, root=ROOT) != experiment_output.resolve():
            print(json.dumps({
                "status": "blocked",
                "mode": arguments.mode,
                "reason_codes": ["EVALUATION_PROFILE_OUTPUT_PATH_FORBIDDEN"],
                "report": None,
            }, ensure_ascii=False))
            return None, 2
        return experiment_output, None
    return arguments.output or _default_output(arguments.mode, paths), None


def _dispatch_mode(
    arguments: argparse.Namespace,
    paths: EvaluationArtifactPaths,
    output: Path,
    evaluation_experiment: EvaluationExperimentContext,
) -> int:
    if arguments.mode == "practical-dry-run":
        return _run_practical(output)
    if arguments.mode == "long-horizon-dry-run":
        return _run_long_horizon(output)
    if arguments.mode == "corrective-ready":
        return _run_corrective_ready(
            paths,
            output,
            profile_name=arguments.profile,
            external_identity=normalize_external_identity(arguments.external_identity),
            evaluation_experiment=evaluation_experiment,
        )
    if arguments.mode == "real-model-preflight":
        return _run_preflight(arguments, output)
    if arguments.mode == "live-model":
        return _run_live(arguments, paths, output, evaluation_experiment)
    if arguments.mode == "adversarial-audit":
        return _run_adversarial(output)
    return _run_dry(arguments, output, evaluation_experiment)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _build_parser().parse_args(argv)
    try:
        evaluation_experiment = evaluation_context(
            arguments.variant_profile,
            experiment_id=arguments.experiment_id,
            trial_id=arguments.trial_id,
        )
    except EvaluationExperimentError as exc:
        print(json.dumps({
            "status": "blocked",
            "mode": arguments.mode,
            "reason_codes": [exc.reason_code],
            "report": None,
        }, ensure_ascii=False))
        return 2
    paths = canonical_artifact_paths(ROOT)
    output, blocked = _select_output(arguments, paths, evaluation_experiment)
    if blocked is not None:
        return blocked
    assert output is not None
    return _dispatch_mode(arguments, paths, output, evaluation_experiment)


if __name__ == "__main__":
    raise SystemExit(main())

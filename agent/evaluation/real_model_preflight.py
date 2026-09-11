"""Pure, zero-provider-call preflight for a future live campaign."""

from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

from agent.evaluation.artifact_paths import canonical_artifact_paths
from agent.evaluation.campaign_artifacts import deterministic_readiness_issues
from agent.evaluation.evaluation_identity import (
    DEFAULT_PROFILE,
    DEFAULT_REAL_MODEL_EPOCH,
    candidate_identity,
    candidate_identity_string,
    fixture_identity,
    model_config_identity,
)
from agent.evaluation.release_prerequisites import (
    project_release_prerequisite_snapshot,
    validate_release_prerequisite_projection,
)
from agent.evaluation.scenario_contracts import H_SERIES_VERSION, RepetitionPolicy
from agent.runtime.filesystem_primitives import write_bytes_atomic

REAL_MODEL_PREFLIGHT_SCHEMA_VERSION = "REAL-MODEL-PREFLIGHT-V1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _read_json(path: Path) -> Mapping[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, Mapping) else None


def _require_equal(
    reasons: list[str],
    value: Any,
    expected: Any,
    missing_code: str,
    mismatch_code: str,
) -> None:
    if value is None:
        reasons.append(missing_code)
    elif value != expected:
        reasons.append(mismatch_code)


def _candidate_checks(
    reasons: list[str],
    artifact: Mapping[str, Any] | None,
    expected: Mapping[str, Any],
    prefix: str,
) -> None:
    if not isinstance(artifact, Mapping):
        reasons.append(f"{prefix}_MISSING")
        return
    stored_candidate = artifact.get("candidate")
    if not isinstance(stored_candidate, Mapping):
        reasons.append(f"{prefix}_CANDIDATE_MISSING")
    elif any(
        stored_candidate.get(field) != expected.get(field)
        for field in ("head", "semantic_candidate_fingerprint", "semantic_manifest_hash")
        if field in stored_candidate
    ) or any(field not in stored_candidate for field in ("head", "semantic_candidate_fingerprint", "semantic_manifest_hash")):
        reasons.append(f"{prefix}_CANDIDATE_MISMATCH")
    _require_equal(
        reasons,
        artifact.get("candidate_identity"),
        candidate_identity_string(expected),
        f"{prefix}_IDENTITY_MISSING",
        f"{prefix}_IDENTITY_MISMATCH",
    )


def _installed_checks(
    reasons: list[str],
    installed: Mapping[str, Any] | None,
    expected_candidate: Mapping[str, Any],
) -> None:
    _candidate_checks(reasons, installed, expected_candidate, "INSTALLED_ACCEPTANCE")
    if not isinstance(installed, Mapping):
        return
    if installed.get("schema_version") is None:
        reasons.append("INSTALLED_ACCEPTANCE_SCHEMA_MISSING")
    if installed.get("status") != "passed" or installed.get("acceptance") is not True:
        reasons.append("INSTALLED_ACCEPTANCE_NOT_CLEAN")
    if str(installed.get("mode", "")).casefold() != "clean-acceptance":
        reasons.append("INSTALLED_ACCEPTANCE_NOT_CLEAN")
    if installed.get("task_files_in_wheel") is not False:
        reasons.append("INSTALLED_WHEEL_CONTENT_INVALID")
    manifest = installed.get("semantic_manifest_hash")
    _require_equal(
        reasons,
        manifest,
        expected_candidate.get("semantic_manifest_hash"),
        "INSTALLED_MANIFEST_MISSING",
        "INSTALLED_MANIFEST_MISMATCH",
    )
    wheel_sha = str(installed.get("wheel_sha256", ""))
    if not _SHA256.fullmatch(wheel_sha):
        reasons.append("INSTALLED_WHEEL_SHA256_MISSING_OR_INVALID")


def _readiness_checks(
    reasons: list[str],
    readiness: Mapping[str, Any] | None,
    expected_candidate: Mapping[str, Any],
    *,
    epoch: str,
    model_config_fingerprint: str,
) -> None:
    _candidate_checks(reasons, readiness, expected_candidate, "DETERMINISTIC_READINESS")
    if not isinstance(readiness, Mapping):
        return
    _require_equal(
        reasons,
        readiness.get("epoch"),
        epoch,
        "DETERMINISTIC_EPOCH_MISSING",
        "DETERMINISTIC_EPOCH_MISMATCH",
    )
    _require_equal(
        reasons,
        readiness.get("semantic_manifest_hash"),
        expected_candidate.get("semantic_manifest_hash"),
        "DETERMINISTIC_MANIFEST_MISSING",
        "DETERMINISTIC_MANIFEST_MISMATCH",
    )
    _require_equal(
        reasons,
        readiness.get("fixture_identity"),
        fixture_identity(),
        "FIXTURE_IDENTITY_MISSING",
        "FIXTURE_IDENTITY_MISMATCH",
    )
    _require_equal(
        reasons,
        readiness.get("h_series_version"),
        H_SERIES_VERSION,
        "H_SERIES_VERSION_MISSING",
        "H_SERIES_VERSION_MISMATCH",
    )
    _require_equal(
        reasons,
        readiness.get("repetition_policy"),
        RepetitionPolicy().to_dict(),
        "REPETITION_POLICY_MISSING",
        "REPETITION_POLICY_MISMATCH",
    )
    if readiness.get("schema_version") is None:
        reasons.append("DETERMINISTIC_READINESS_SCHEMA_MISSING")
    model_schema = readiness.get("model_identity_schema")
    if not isinstance(model_schema, Mapping):
        model_schema = readiness.get("model_identity")
    stored_model_fingerprint = (
        model_schema.get("model_config_fingerprint")
        if isinstance(model_schema, Mapping)
        else None
    )
    _require_equal(
        reasons,
        stored_model_fingerprint,
        model_config_fingerprint,
        "MODEL_CONFIG_FINGERPRINT_MISSING",
        "MODEL_CONFIG_FINGERPRINT_MISMATCH",
    )
    if readiness.get("campaign_started") is not False:
        reasons.append("DETERMINISTIC_CAMPAIGN_ALREADY_STARTED")
    reasons.extend(deterministic_readiness_issues(readiness))


def validate_real_model_preflight(
    repo_root: str | Path,
    *,
    profile_name: str = DEFAULT_PROFILE,
    epoch: str = DEFAULT_REAL_MODEL_EPOCH,
    external_identity: str | None = None,
    installed_acceptance: Mapping[str, Any] | None = None,
    deterministic_readiness: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate current-candidate artifacts without constructing a provider."""

    root = Path(repo_root).resolve()
    paths = canonical_artifact_paths(root)
    current = candidate_identity(root)
    expected_model = model_config_identity(
        root,
        profile_name=profile_name,
        evidence_level="real_model",
        external_identity=external_identity,
    )
    installed = (
        installed_acceptance
        if installed_acceptance is not None
        else _read_json(paths.installed_acceptance)
    )
    readiness = (
        deterministic_readiness
        if deterministic_readiness is not None
        else _read_json(paths.corrective_ready)
    )
    reasons: list[str] = []
    _installed_checks(reasons, installed, current)
    _readiness_checks(
        reasons,
        readiness,
        current,
        epoch=epoch,
        model_config_fingerprint=str(expected_model["model_config_fingerprint"]),
    )
    readiness_issues = deterministic_readiness_issues(readiness)
    prerequisite_projection = project_release_prerequisite_snapshot(installed, readiness)
    reasons.extend(validate_release_prerequisite_projection(prerequisite_projection))
    result: dict[str, Any] = {
        "schema_version": REAL_MODEL_PREFLIGHT_SCHEMA_VERSION,
        "status": "pass" if not reasons else "blocked",
        "ready": not reasons,
        "mode": "real-model-preflight",
        "reason_codes": list(dict.fromkeys(reasons)),
        "candidate": current,
        "candidate_identity": candidate_identity_string(current),
        "semantic_manifest_hash": current["semantic_manifest_hash"],
        "fixture_identity": fixture_identity(),
        "h_series_version": H_SERIES_VERSION,
        "repetition_policy": RepetitionPolicy().to_dict(),
        "epoch": epoch,
        "requested_profile": profile_name,
        "requested_model_config_fingerprint": expected_model["model_config_fingerprint"],
        "installed_acceptance": deepcopy(
            prerequisite_projection["installed_acceptance"]
        ),
        "deterministic_readiness": {
            "candidate_identity": prerequisite_projection["deterministic_readiness"].get(
                "candidate_identity"
            ),
            "complete": bool(
                isinstance(readiness, Mapping)
                and not readiness_issues
                and not validate_release_prerequisite_projection(prerequisite_projection)
            ),
        },
        "artifact_paths": {
            "installed_acceptance": ".audit-local/out/installed-acceptance.json",
            "deterministic_readiness": ".audit-local/out/evaluation-corrective-ready.json",
            "preflight": ".audit-local/out/real-model-preflight.json",
        },
        "provider_calls": 0,
        "network_calls": 0,
    }
    # These are the exact objects validated above.  Downstream report code
    # must consume these snapshots rather than reopening the artifact paths.
    result["prerequisite_snapshots"] = {
        **deepcopy(prerequisite_projection),
    }
    return result


def build_real_model_preflight(
    repo_root: str | Path,
    *,
    profile_name: str = DEFAULT_PROFILE,
    epoch: str = DEFAULT_REAL_MODEL_EPOCH,
    external_identity: str | None = None,
    output_path: str | Path | None = None,
    installed_acceptance: Mapping[str, Any] | None = None,
    deterministic_readiness: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    result = validate_real_model_preflight(
        repo_root,
        profile_name=profile_name,
        epoch=epoch,
        external_identity=external_identity,
        installed_acceptance=installed_acceptance,
        deterministic_readiness=deterministic_readiness,
    )
    destination = Path(output_path) if output_path is not None else canonical_artifact_paths(repo_root).real_model_preflight
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    write_bytes_atomic(destination, payload)
    return result


__all__ = [
    "REAL_MODEL_PREFLIGHT_SCHEMA_VERSION",
    "build_real_model_preflight",
    "validate_real_model_preflight",
]

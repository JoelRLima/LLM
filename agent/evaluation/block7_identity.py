"""Immutable Block 7 candidate, fixture, and model/config identities.

The first Block 7 implementation exposed a broad source hash and a planned
model label.  The corrected campaign keeps that compatibility information but
adds a semantic manifest and a canonical non-secret model/config fingerprint.
Documentation can therefore change independently while any execution,
evaluation, fixture, or provider change invalidates a campaign epoch.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any, Mapping

from agent.evaluation.block7 import H_SERIES_VERSION, RepetitionPolicy
from agent.evaluation.block7_model_identity import (
    DEFAULT_PROFILE,
    fake_model_identity,
    model_config_identity,
    normalize_endpoint_identity,
    planned_model_profile,
)

CAMPAIGN_SCHEMA_VERSION = "B7-CAMPAIGN-V2.0"
DEFAULT_DRY_RUN_EPOCH = "B7-DRY-RUN-V2"
DEFAULT_REAL_MODEL_EPOCH = "B7-REAL-MODEL-EPOCH-2"


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _sha256(value: bytes | str) -> str:
    payload = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(payload).hexdigest()


def _git_head(repo_root: Path) -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _included_files(root: Path, roots: tuple[str, ...], excluded: set[str]) -> list[Path]:
    files: list[Path] = []
    for relative_root in roots:
        candidate = root / relative_root
        if candidate.is_file():
            files.append(candidate)
        elif candidate.is_dir():
            files.extend(
                path
                for path in candidate.rglob("*")
                if path.is_file() and not (set(path.relative_to(root).parts) & excluded)
            )
    return sorted(set(files), key=lambda item: item.relative_to(root).as_posix())


def source_fingerprint(repo_root: str | Path) -> str:
    """Compatibility hash used by the earlier Block 7 reports."""

    root = Path(repo_root).resolve()
    roots = (
        "agent", "scripts", "tests", "docs", ".github", "pyproject.toml", "setup.py",
        "setup.cfg", "requirements.txt", "requirements-dev.txt",
    )
    excluded = {
        ".git", ".venv", ".audit-local", "__pycache__", ".pytest_cache", ".mypy_cache",
        ".ruff_cache", "TASK_CONTRACT.md", "TASK_SPEC.md",
    }
    digest = hashlib.sha256()
    for path in _included_files(root, roots, excluded):
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def semantic_candidate_manifest(repo_root: str | Path) -> list[dict[str, str]]:
    """List every repository input that can affect campaign semantics.

    Runtime/evaluation code, the campaign runner, provider configuration, and
    packaging inputs are included. Documentation and tests are separate so an
    explanatory update cannot silently change an execution epoch.
    """

    root = Path(repo_root).resolve()
    roots = (
        "agent", "scripts", "pyproject.toml", "setup.py", "setup.cfg",
        "requirements.txt", "requirements-dev.txt",
    )
    excluded = {
        ".git", ".venv", ".audit-local", "__pycache__", ".pytest_cache", ".mypy_cache",
        ".ruff_cache", "TASK_CONTRACT.md", "TASK_SPEC.md",
    }
    manifest: list[dict[str, str]] = []
    for path in _included_files(root, roots, excluded):
        relative = path.relative_to(root).as_posix()
        manifest.append({"path": relative, "sha256": _sha256(path.read_bytes())})
    return manifest


def semantic_manifest_hash(manifest: list[dict[str, str]]) -> str:
    return _sha256(_canonical_json(manifest))


def semantic_candidate_fingerprint(repo_root: str | Path) -> str:
    """Hash semantic candidate paths and bytes, excluding documentation."""

    root = Path(repo_root).resolve()
    digest = hashlib.sha256()
    for item in semantic_candidate_manifest(root):
        path = str(item["path"])
        digest.update(path.encode("utf-8"))
        digest.update(b"\0")
        digest.update((root / path).read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def documentation_fingerprint(repo_root: str | Path) -> str:
    root = Path(repo_root).resolve()
    files = _included_files(
        root,
        ("docs",),
        {".git", ".venv", ".audit-local", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"},
    )
    payload = [{"path": p.relative_to(root).as_posix(), "sha256": _sha256(p.read_bytes())} for p in files]
    return _sha256(_canonical_json(payload))


def candidate_identity(repo_root: str | Path) -> dict[str, str]:
    root = Path(repo_root).resolve()
    manifest = semantic_candidate_manifest(root)
    semantic = semantic_candidate_fingerprint(root)
    manifest_hash = semantic_manifest_hash(manifest)
    return {
        "head": _git_head(root),
        "source_fingerprint": source_fingerprint(root),
        "semantic_candidate_fingerprint": semantic,
        "semantic_manifest_hash": manifest_hash,
        "documentation_fingerprint": documentation_fingerprint(root),
    }


def candidate_identity_string(candidate: Mapping[str, Any]) -> str:
    return ":".join(
        str(candidate.get(key, ""))
        for key in ("head", "semantic_candidate_fingerprint", "semantic_manifest_hash")
    )


def fixture_identity() -> str:
    """Fingerprint H-series semantics and fixtures without absolute paths."""

    from agent.evaluation.block7 import H_SERIES

    payload: list[dict[str, Any]] = []
    for scenario in H_SERIES:
        payload.append({
            "h_id": scenario.h_id,
            "semantic_intent": scenario.semantic_intent,
            "fixture_id": scenario.fixture_id,
            "required_repetitions": scenario.required_repetitions,
            "arms": [
                {
                    "arm_id": arm.arm_id,
                    "objective": arm.objective,
                    "initial_files": dict(sorted(arm.initial_files.items())),
                    "expectation": {
                        "success": arm.expectation.success,
                        "files": [item.__dict__ for item in arm.expectation.files],
                        "unchanged_files": list(arm.expectation.unchanged_files),
                        "allowed_changed_files": list(arm.expectation.allowed_changed_files),
                        "answer_contains": list(arm.expectation.answer_contains),
                        "answer_not_contains": list(arm.expectation.answer_not_contains),
                        "max_steps": arm.expectation.max_steps,
                    },
                    "oracle": arm.oracle,
                }
                for arm in scenario.arms
            ],
        })
    return _sha256(_canonical_json(payload))


def campaign_config(
    repo_root: str | Path,
    *,
    output_dir: str | Path,
    profile_name: str = DEFAULT_PROFILE,
    epoch: str = DEFAULT_REAL_MODEL_EPOCH,
) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    candidate = candidate_identity(root)
    manifest = semantic_candidate_manifest(root)
    model_identity = model_config_identity(root, profile_name=profile_name)
    output = Path(output_dir)
    try:
        output_label = output.resolve().relative_to(root).as_posix()
    except ValueError:
        output_label = output.name
    return {
        "schema_version": CAMPAIGN_SCHEMA_VERSION,
        "scenario_set_version": H_SERIES_VERSION,
        "fixture_identity": fixture_identity(),
        "epoch": epoch,
        "candidate": candidate,
        "candidate_identity": candidate_identity_string(candidate),
        "semantic_candidate_manifest": manifest,
        "semantic_manifest_hash": semantic_manifest_hash(manifest),
        "model_identity": model_identity,
        "model_config_fingerprint": model_identity["model_config_fingerprint"],
        "repetition_policy": RepetitionPolicy().to_dict(),
        "output_dir": output_label,
        "secret_policy": {
            "allowed": "bounded request/response/decision evidence only",
            "redacted_forms": ["Authorization", "Bearer", "api_key=", "password=", "token="],
            "wholesale_environment_export": False,
        },
        "resume_policy": "resume only with exact candidate, semantic manifest, epoch, model/config, H-series, and fixture fingerprints",
    }


def resume_compatible(existing: Mapping[str, Any], current: Mapping[str, Any]) -> bool:
    """Require exact semantic and provider identity before resuming."""

    keys = (
        "schema_version", "scenario_set_version", "fixture_identity", "epoch",
        "candidate", "candidate_identity", "semantic_manifest_hash", "model_identity",
        "model_config_fingerprint", "repetition_policy",
    )
    return all(existing.get(key) == current.get(key) for key in keys)


__all__ = [
    "CAMPAIGN_SCHEMA_VERSION", "DEFAULT_DRY_RUN_EPOCH", "DEFAULT_PROFILE",
    "DEFAULT_REAL_MODEL_EPOCH", "campaign_config", "candidate_identity",
    "candidate_identity_string", "documentation_fingerprint", "fake_model_identity",
    "fixture_identity", "model_config_identity", "normalize_endpoint_identity",
    "planned_model_profile", "resume_compatible", "semantic_candidate_fingerprint",
    "semantic_candidate_manifest", "semantic_manifest_hash", "source_fingerprint",
]

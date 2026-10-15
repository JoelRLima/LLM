"""Local-only adapter around portable W23 evidence and explicit K7 selection."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from distribution.candidate_manifest import CandidateManifestError, parse_manifest, validate_repo_path
from scripts.w23_architecture.evidence import (
    EvidenceError,
    artifact_ref,
    verify_witness,
)

WITNESS_PATH = "quality/w23/acceptance_tooling_witness_v001.json"
PREDECESSOR_PATH = "quality/w23/compatibility_closure_evidence.json"
PRODUCTION_PATH = "quality/w23/cycle_report_after_compatibility_retirement.json"
SNAPSHOT_PATH = "quality/w23/historical_tooling/test_w23_cycle_policy.pre_k4.txt"
DEFAULT_AUTHORITY_PATH = ".agent-local/AUTHORITY_MANIFEST.json"
LOCAL_GIT_METADATA_SENTINELS = frozenset({".git/index", ".git/info/exclude"})


class AcceptanceError(ValueError):
    """Local W23 acceptance inputs failed closed validation."""


def _validate_local_protected_path(path: str) -> str:
    """Validate local authority paths without admitting Git internals as source paths."""
    if path in LOCAL_GIT_METADATA_SENTINELS:
        return path
    validated_path = validate_repo_path(path)
    if not isinstance(validated_path, str):
        raise CandidateManifestError("repository path validator returned a non-string path")
    return validated_path


def _validate_authority_semantics(authority: Mapping[str, Any]) -> None:
    """Bind the authority to its stable schema, selector, and canonical artifacts."""
    if type(authority.get("schema_version")) is not int or authority["schema_version"] != 1:
        raise AcceptanceError("selected authority schema is unsupported")
    if authority.get("sole_active_selector") != DEFAULT_AUTHORITY_PATH:
        raise AcceptanceError("selected authority selector path is unexpected")
    canonical_artifacts = {
        "canonical_contract": (
            ".agent-local/W23_TASK_CONTRACT_v007_RESUME.md",
            "af8a9a8f09be60261a30120d5fe44bedc518c8e74ebf8d842858ca9bd8953965",
        ),
        "canonical_spec": (
            ".agent-local/W23_TASK_SPEC_v007_RESUME.md",
            "3cd008124d6379362cef3997c76a748a131dedd0b5f3e94b01900bd930746ed7",
        ),
    }
    for name, (expected_path, expected_sha256) in canonical_artifacts.items():
        reference = authority.get(name)
        if (not isinstance(reference, dict) or reference.get("path") != expected_path
                or reference.get("sha256") != expected_sha256):
            raise AcceptanceError("selected authority canonical artifact reference is unexpected")


def _parse_authority(authority_bytes: bytes) -> tuple[Mapping[str, Any], dict[str, str]]:
    try:
        authority = json.loads(authority_bytes.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise AcceptanceError("selected authority is not UTF-8 JSON") from exc
    if not isinstance(authority, dict):
        raise AcceptanceError("selected authority must be a JSON object")
    _validate_authority_semantics(authority)
    rows = authority.get("protected_hashes_at_install")
    if not isinstance(rows, list):
        raise AcceptanceError("selected authority has no protected workspace bindings")
    declared: dict[str, str] = {}
    ordered: list[str] = []
    for row in rows:
        if (not isinstance(row, dict) or set(row) != {"path", "sha256"}
                or not isinstance(row["path"], str) or not isinstance(row["sha256"], str)
                or len(row["sha256"]) != 64
                or any(char not in "0123456789abcdef" for char in row["sha256"])):
            raise AcceptanceError("selected authority has a malformed protected binding")
        try:
            path = _validate_local_protected_path(row["path"])
        except CandidateManifestError as exc:
            raise AcceptanceError("selected authority has an unsafe protected path") from exc
        if path in declared:
            raise AcceptanceError("selected authority has a duplicate protected path")
        declared[path] = row["sha256"]
        ordered.append(path)
    if ordered != sorted(ordered):
        raise AcceptanceError("selected authority protected bindings are not sorted")
    return authority, declared


def verify_authority_bytes(
    authority_bytes: bytes,
    expected_sha256: str,
    protected_bytes: Mapping[str, bytes],
    protected_sha256: Mapping[str, str],
) -> Mapping[str, Any]:
    """Verify current local authority and explicitly bound protected bytes."""
    if (len(expected_sha256) != 64 or any(c not in "0123456789abcdef" for c in expected_sha256)
            or hashlib.sha256(authority_bytes).hexdigest() != expected_sha256):
        raise AcceptanceError("selected local authority digest mismatch")
    if set(protected_bytes) != set(protected_sha256):
        raise AcceptanceError("protected workspace binding membership mismatch")
    for path, expected in protected_sha256.items():
        if hashlib.sha256(protected_bytes[path]).hexdigest() != expected:
            raise AcceptanceError(f"protected workspace binding changed: {path}")
    authority, declared = _parse_authority(authority_bytes)
    if declared != dict(protected_sha256):
        raise AcceptanceError("protected workspace set differs from selected authority")
    return authority


def explicit_candidate(data: bytes | None, expected_sha256: str | None) -> tuple[str, dict[str, Any] | None]:
    """Parse only the caller-selected manifest; absence stays NOT_SELECTED."""
    if data is None and expected_sha256 is None:
        return "NOT_SELECTED", None
    if data is None or expected_sha256 is None:
        raise AcceptanceError("candidate selection requires both bytes and digest")
    try:
        return "SELECTED", parse_manifest(data, expected_sha256)
    except CandidateManifestError as exc:
        raise AcceptanceError("explicit candidate selection is invalid") from exc


def verify_portable_files(
    root: Path,
    *,
    witness_path: str = WITNESS_PATH,
) -> dict[str, Any]:
    """Load fixed versioned evidence and delegate all checks to portable API."""
    try:
        witness_bytes = (root / witness_path).read_bytes()
        witness = json.loads(witness_bytes.decode("utf-8"))
        tooling = {row["path"]: (root / row["path"]).read_bytes() for row in witness["current_tooling"]}
        snapshots = {row["path"]: (root / row["path"]).read_bytes()
                     for row in witness["historical_tooling_snapshots"]}
        return verify_witness(
            witness_bytes,
            (root / PREDECESSOR_PATH).read_bytes(),
            (root / PRODUCTION_PATH).read_bytes(),
            tooling,
            snapshots,
        )
    except (OSError, KeyError, TypeError, json.JSONDecodeError, UnicodeError, EvidenceError) as exc:
        raise AcceptanceError("portable W23 evidence verification failed") from exc


def run_acceptance(
    root: Path,
    *,
    authority_sha256: str,
    protected_sha256: Mapping[str, str] | None = None,
    authority_path: str = DEFAULT_AUTHORITY_PATH,
    selected_manifest_path: str | None = None,
    selected_manifest_sha256: str | None = None,
) -> dict[str, Any]:
    """Verify explicit local authority, portable witness, then optional selection."""
    try:
        authority_bytes = (root / authority_path).read_bytes()
    except OSError as exc:
        raise AcceptanceError("required local authority or protected file is missing") from exc
    if hashlib.sha256(authority_bytes).hexdigest() != authority_sha256:
        raise AcceptanceError("selected local authority digest mismatch")
    authority, declared = _parse_authority(authority_bytes)
    if protected_sha256 is not None and dict(protected_sha256) != declared:
        raise AcceptanceError("caller protected bindings differ from selected authority")
    try:
        protected = {path: (root / path).read_bytes() for path in declared}
    except OSError as exc:
        raise AcceptanceError("required local authority or protected file is missing") from exc
    authority = verify_authority_bytes(authority_bytes, authority_sha256, protected, declared)
    witness = verify_portable_files(root)
    if selected_manifest_path is None and selected_manifest_sha256 is None:
        selection, manifest = "NOT_SELECTED", None
    elif selected_manifest_path is None or selected_manifest_sha256 is None:
        raise AcceptanceError("candidate selection requires an explicit path and digest")
    else:
        try:
            selected_path = artifact_ref({"path": selected_manifest_path, "sha256": selected_manifest_sha256})["path"]
            data = (root / selected_path).read_bytes()
        except (OSError, EvidenceError) as exc:
            raise AcceptanceError("explicit candidate manifest is unavailable") from exc
        selection, manifest = explicit_candidate(data, selected_manifest_sha256)
    return {"authority": authority, "witness": witness, "candidate_status": selection,
            "candidate": manifest}

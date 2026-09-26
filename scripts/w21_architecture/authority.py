"""Generic authenticated authority loading for epoch and corrective envelopes."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


class AuthorityError(ValueError):
    """Raised when an externally authenticated authority is unusable."""


@dataclass(frozen=True)
class ActiveAuthority:
    artifact: str
    authority_id: str
    status: str
    base_sha: str
    base_tree: str | None
    worktree: Path
    branch: str
    write_paths: frozenset[str]
    new_path_prefixes: tuple[str, ...]
    required_new_paths: tuple[str, ...]
    forbidden_paths: frozenset[str]
    forbidden_path_prefixes: tuple[str, ...]
    required_transition_violation_removals: frozenset[str]
    accepted_removed_transition_violation_ids: frozenset[str]
    higher_authority: Mapping[str, Any]
    raw: Mapping[str, Any]


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def _required(mapping: Mapping[str, Any], key: str, context: str) -> Any:
    if key not in mapping:
        raise AuthorityError(f"{context} is missing required field {key!r}")
    return mapping[key]


def _string(value: Any, label: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value):
        raise AuthorityError(f"{label} must be a non-empty string")
    return value


def _string_sequence(value: Any, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise AuthorityError(f"{label} must be a list of strings")
    return tuple(value)


def _scope_values(raw: Mapping[str, Any], artifact: str) -> tuple[Mapping[str, Any], str, str | None, Path, str, Mapping[str, Any]]:
    if artifact == "W21_EPOCH_AUTHORITY":
        baseline = _required(raw, "baseline", "epoch authority")
        worktree = _required(raw, "worktree", "epoch authority")
        scope = _required(raw, "write_scope", "epoch authority")
        if not isinstance(baseline, dict) or not isinstance(worktree, dict) or not isinstance(scope, dict):
            raise AuthorityError("epoch authority baseline/worktree/write_scope must be objects")
        authority_id = _string(_required(raw, "epoch_id", "epoch authority"), "epoch_id")
        _string(_required(baseline, "epoch_base_sha", "epoch baseline"), "epoch_base_sha")
        base_tree_value = baseline.get("epoch_base_tree")
        base_tree = _string(base_tree_value, "epoch_base_tree") if base_tree_value is not None else None
        branch = _string(_required(worktree, "local_branch", "epoch worktree"), "local_branch")
        path = Path(_string(_required(worktree, "absolute_path", "epoch worktree"), "absolute_path"))
        return scope, authority_id, base_tree, path, branch, worktree

    if artifact == "W21_CORRECTIVE_AUTHORITY":
        baseline = _required(raw, "base", "corrective authority")
        worktree = _required(raw, "worktree", "corrective authority")
        if not isinstance(baseline, dict) or not isinstance(worktree, dict):
            raise AuthorityError("corrective authority base/worktree must be objects")
        authority_id = _string(_required(raw, "corrective_id", "corrective authority"), "corrective_id")
        _string(_required(baseline, "sha", "corrective base"), "base.sha")
        base_tree_value = baseline.get("tree")
        base_tree = _string(base_tree_value, "base.tree") if base_tree_value is not None else None
        branch = _string(_required(worktree, "branch", "corrective worktree"), "branch")
        path = Path(_string(_required(worktree, "absolute_path", "corrective worktree"), "absolute_path"))
        scope = {
            "write_paths": _required(raw, "write_paths", "corrective authority"),
            "new_path_prefixes": _required(raw, "new_path_prefixes", "corrective authority"),
            "required_new_paths": _required(raw, "required_outputs", "corrective authority"),
        }
        return scope, authority_id, base_tree, path, branch, worktree

    raise AuthorityError(f"unknown authority artifact {artifact!r}")


def load_active_authority(path: Path, *, expected_sha256: str) -> ActiveAuthority:
    """Load an authority only after hashing its raw bytes against external trust."""

    if not expected_sha256:
        raise AuthorityError("externally supplied expected authority SHA-256 is mandatory")
    path = Path(path)
    try:
        raw_bytes = path.read_bytes()
    except OSError as exc:
        raise AuthorityError(f"cannot read authority {path}: {exc}") from exc
    actual = sha256_bytes(raw_bytes)
    if actual.lower() != expected_sha256.lower():
        raise AuthorityError(f"authority SHA-256 mismatch: expected {expected_sha256}, got {actual}")
    try:
        document = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AuthorityError(f"authority is not valid UTF-8 JSON: {path}") from exc
    if not isinstance(document, dict):
        raise AuthorityError("authority root must be a JSON object")
    artifact = _string(_required(document, "artifact", "authority"), "artifact")
    if artifact not in {"W21_EPOCH_AUTHORITY", "W21_CORRECTIVE_AUTHORITY"}:
        raise AuthorityError(f"unknown authority artifact {artifact!r}")
    status = _string(_required(document, "status", "authority"), "status")
    if status != "FROZEN":
        raise AuthorityError(f"authority status must be FROZEN, got {status!r}")

    scope, authority_id, base_tree, worktree, branch, worktree_document = _scope_values(document, artifact)
    base_sha = (
        document["baseline"]["epoch_base_sha"]
        if artifact == "W21_EPOCH_AUTHORITY"
        else document["base"]["sha"]
    )
    base_sha = _string(base_sha, "base SHA")
    write_paths = frozenset(_string_sequence(_required(scope, "write_paths", "write scope"), "write_paths"))
    new_prefixes = _string_sequence(_required(scope, "new_path_prefixes", "write scope"), "new_path_prefixes")
    required_new = _string_sequence(_required(scope, "required_new_paths", "write scope"), "required_new_paths")
    forbidden_paths = frozenset(
        _string_sequence(document.get("forbidden_paths", []), "forbidden_paths")
    )
    forbidden_prefixes = _string_sequence(
        document.get("forbidden_path_prefixes", []), "forbidden_path_prefixes"
    )
    required_removals = frozenset(
        _string_sequence(document.get("required_transition_violation_removals", []), "required_transition_violation_removals")
    )
    accepted_removed = frozenset(
        _string_sequence(document.get("accepted_removed_transition_violation_ids", []), "accepted_removed_transition_violation_ids")
    )
    higher = document.get("higher_authority", {})
    if not isinstance(higher, dict):
        raise AuthorityError("higher_authority must be an object")

    del worktree_document
    return ActiveAuthority(
        artifact=artifact,
        authority_id=authority_id,
        status=status,
        base_sha=base_sha,
        base_tree=base_tree,
        worktree=worktree,
        branch=branch,
        write_paths=write_paths,
        new_path_prefixes=new_prefixes,
        required_new_paths=required_new,
        forbidden_paths=forbidden_paths,
        forbidden_path_prefixes=forbidden_prefixes,
        required_transition_violation_removals=required_removals,
        accepted_removed_transition_violation_ids=accepted_removed,
        higher_authority=higher,
        raw=document,
    )


def _active_amendment_declaration(authority: ActiveAuthority) -> tuple[Path, str] | None:
    higher = authority.higher_authority
    path_value = higher.get("active_amendment_absolute_path")
    expected = higher.get("active_amendment_sha256")
    if path_value is None and expected is None:
        return None
    if not isinstance(path_value, str) or not path_value:
        raise AuthorityError("active amendment absolute path is missing")
    if not isinstance(expected, str) or not expected:
        raise AuthorityError("active amendment SHA-256 is missing")
    return Path(path_value), expected


def _read_authenticated_amendment(path: Path, expected: str) -> Mapping[str, Any]:
    try:
        raw_bytes = path.read_bytes()
    except OSError as exc:
        raise AuthorityError(f"cannot read active amendment {path}: {exc}") from exc
    actual = sha256_bytes(raw_bytes)
    if actual.lower() != expected.lower():
        raise AuthorityError(f"active amendment SHA-256 mismatch: expected {expected}, got {actual}")
    try:
        document = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AuthorityError(f"active amendment is not valid UTF-8 JSON: {path}") from exc
    if not isinstance(document, dict):
        raise AuthorityError("active amendment root must be a JSON object")
    if document.get("artifact") != "W21_AUTHORITY_AMENDMENT":
        raise AuthorityError("active amendment artifact is invalid")
    if document.get("status") != "FROZEN":
        raise AuthorityError("active amendment status must be FROZEN")
    if not isinstance(document.get("amendment_id"), str) or not document["amendment_id"]:
        raise AuthorityError("active amendment_id is missing")
    return document


def load_active_amendment(authority: ActiveAuthority) -> Mapping[str, Any] | None:
    """Authenticate and parse an amendment declared by an active authority."""

    declaration = _active_amendment_declaration(authority)
    if declaration is None:
        return None
    return _read_authenticated_amendment(*declaration)


def authority_hash_targets(authority: ActiveAuthority) -> dict[str, Path]:
    """Resolve declared higher-authority files without inventing new paths."""

    targets: dict[str, Path] = {}
    higher = authority.higher_authority
    for key, value in higher.items():
        if key.endswith("_absolute_path") and isinstance(value, str):
            targets[key.removesuffix("_absolute_path") + "_sha256"] = Path(value)
    authority_root = higher.get("authority_root")
    if isinstance(authority_root, str) and authority_root:
        root = Path(authority_root)
        targets.setdefault("global_frozen_manifest_sha256", root / "global" / "WAVE_21_AUTHORITY_MANIFEST_v003_FROZEN.json")
        targets.setdefault("freeze_audit_sha256", root / "global" / "WAVE_21_v003_FREEZE_AUDIT.md")
        targets.setdefault("shared_seam_plan_sha256", root / "architecture" / "shared-seam-plan.json")
    return targets

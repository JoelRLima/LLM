"""Strict v003 release manifest and self-contained bundle validation."""

from __future__ import annotations

import hashlib
import json
import re
import stat
import zipfile
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Mapping

from .payload import PayloadValidationError, archive_member_paths, validate_inventory
from .release_identity import (
    APPLICATION_NAMESPACE,
    APPLICATION_WHEEL,
    BOOTSTRAP_PIP_LOCK,
    BOOTSTRAP_PIP_SHA256,
    BOOTSTRAP_PIP_VERSION,
    BOOTSTRAP_PIP_WHEEL,
    BUNDLE_MEMBERS,
    CLI_NAME,
    DISPLAY_NAME,
    DISTRIBUTION_NAME,
    IDENTITY_STATUS,
    INSTALL_SCOPE,
    PAYLOAD_ARCHIVE,
    PAYLOAD_INVENTORY,
    PLATFORM,
    PYTHON_BUILD_DATE,
    PYTHON_BUILD_IDENTITY,
    PYTHON_IMPLEMENTATION,
    PYTHON_PLATFORM,
    PYTHON_SOURCE_ARTIFACT_SHA256,
    PYTHON_SOURCE_ARTIFACT_URL,
    PYTHON_VERSION,
    RELEASE_VERSION,
    RUNTIME_LOCK,
    SCHEMA_VERSION,
    UV_ASSET_URL,
    UV_SHA256,
    UV_SIGNATURE,
    UV_VERSION,
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_TREE = re.compile(r"^[0-9a-f]{40}$")
_CANDIDATE = re.compile(r"^w18-[0-9a-f]{32}$")
_SOURCE_STATUSES = {"uncommitted_candidate", "committed_release"}

TOP_LEVEL_KEYS = {
    "schema_version",
    "product_identity",
    "source",
    "application",
    "payload",
    "runtime_lock",
    "bootstrap_pip_lock",
    "platform",
    "install_scope",
    "build_tools",
    "python",
    "candidate_id",
}
_IDENTITY_KEYS = {"display_name", "cli_name", "distribution_name", "application_namespace", "identity_status"}
_SOURCE_KEYS = {"status", "base_commit", "commit", "tree"}
_APPLICATION_KEYS = {"version", "wheel", "wheel_sha256"}
_PAYLOAD_KEYS = {"path", "sha256", "inventory", "inventory_sha256"}
_ARTIFACT_KEYS = {"path", "sha256"}
_BUILD_TOOLS_KEYS = {"uv", "pip"}
_UV_KEYS = {"version", "asset_url", "sha256", "expected_signature"}
_PIP_KEYS = {"version", "wheel", "wheel_sha256"}
_PYTHON_KEYS = {
    "implementation",
    "exact_version",
    "platform",
    "build_identity",
    "build_date",
    "source_artifact_url",
    "source_artifact_sha256",
}
_SIGNATURE_KEYS = {
    "status",
    "signer_subject",
    "signer_thumbprint",
    "timestamp_subject",
    "timestamp_thumbprint",
    "evidence",
}


class ManifestValidationError(ValueError):
    """Raised when a release manifest fails closed."""


def canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        stream = path.open("rb")
    except OSError as exc:
        raise ManifestValidationError(f"cannot read artifact: {path}") from exc
    with stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_linklike(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError as exc:
        raise ManifestValidationError(f"cannot inspect bundle member: {path}") from exc
    if stat.S_ISLNK(info.st_mode):
        return True
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(getattr(info, "st_file_attributes", 0) & reparse)


def candidate_id(manifest: Mapping[str, Any]) -> str:
    """Derive artifact identity without binding it to future commit sealing."""

    identity = manifest["product_identity"]
    application = manifest["application"]
    payload = manifest["payload"]
    runtime_lock = manifest["runtime_lock"]
    bootstrap_lock = manifest["bootstrap_pip_lock"]
    build_tools = manifest["build_tools"]
    projection = {
        "application_namespace": identity["application_namespace"],
        "application_version": application["version"],
        "application_wheel": application["wheel"],
        "application_wheel_sha256": application["wheel_sha256"],
        "bootstrap_pip_lock_path": bootstrap_lock["path"],
        "bootstrap_pip_lock_sha256": bootstrap_lock["sha256"],
        "build_tools": {
            "pip": dict(build_tools["pip"]),
            "uv": {
                "asset_url": build_tools["uv"]["asset_url"],
                "sha256": build_tools["uv"]["sha256"],
                "version": build_tools["uv"]["version"],
            },
        },
        "distribution_name": identity["distribution_name"],
        "install_scope": manifest["install_scope"],
        "payload_inventory_path": payload["inventory"],
        "payload_inventory_sha256": payload["inventory_sha256"],
        "payload_path": payload["path"],
        "payload_sha256": payload["sha256"],
        "platform": manifest["platform"],
        "python": dict(manifest["python"]),
        "runtime_lock_path": runtime_lock["path"],
        "runtime_lock_sha256": runtime_lock["sha256"],
        "schema_version": manifest["schema_version"],
        "source_tree": manifest["source"]["tree"],
    }
    return "w18-" + sha256_bytes(canonical_json_bytes(projection))[:32]


def _mapping(value: Any, keys: set[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ManifestValidationError(f"{label} must be an object")
    unknown = set(value) - keys
    missing = keys - set(value)
    if unknown:
        raise ManifestValidationError(f"unknown {label} key(s): {sorted(unknown)}")
    if missing:
        raise ManifestValidationError(f"missing {label} key(s): {sorted(missing)}")
    return value


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ManifestValidationError(f"{label} must be a non-empty string")
    return value


def _sha(value: Any, label: str) -> str:
    value = _string(value, label)
    if not _SHA256.fullmatch(value):
        raise ManifestValidationError(f"{label} is not a lowercase SHA-256")
    return value


def bundle_relative_path(value: Any, label: str) -> str:
    """Validate a slash-separated path without normalization-after-traversal."""

    value = _string(value, label)
    if "\x00" in value or "\\" in value or ":" in value:
        raise ManifestValidationError(f"{label} must be a portable bundle-relative path")
    posix = PurePosixPath(value)
    windows = PureWindowsPath(value)
    if posix.is_absolute() or windows.is_absolute() or windows.drive or windows.anchor:
        raise ManifestValidationError(f"{label} must not be absolute")
    if any(part in {"", ".", ".."} for part in value.split("/")):
        raise ManifestValidationError(f"{label} contains an unsafe path component")
    return value


def _artifact(value: Any, label: str, expected_path: str) -> Mapping[str, Any]:
    artifact = _mapping(value, _ARTIFACT_KEYS, label)
    path = bundle_relative_path(artifact["path"], f"{label}.path")
    if path != expected_path:
        raise ManifestValidationError(f"{label}.path must be {expected_path}")
    _sha(artifact["sha256"], f"{label}.sha256")
    return artifact


def _validate_document_shape(document: Any) -> Mapping[str, Any]:
    if not isinstance(document, Mapping):
        raise ManifestValidationError("manifest must be an object")
    _mapping(document, TOP_LEVEL_KEYS, "top-level")
    if document["schema_version"] != SCHEMA_VERSION:
        raise ManifestValidationError("unknown schema version")
    return document


def _validate_identity(document: Mapping[str, Any]) -> None:
    identity = _mapping(document["product_identity"], _IDENTITY_KEYS, "product_identity")
    expected = {
        "display_name": DISPLAY_NAME,
        "cli_name": CLI_NAME,
        "distribution_name": DISTRIBUTION_NAME,
        "application_namespace": APPLICATION_NAMESPACE,
        "identity_status": IDENTITY_STATUS,
    }
    if dict(identity) != expected:
        raise ManifestValidationError("product identity does not match the provisional W18 identity")


def _validate_source_commits(source: Mapping[str, Any], status: str) -> tuple[str, str | None]:
    base_commit = _string(source["base_commit"], "source.base_commit")
    if not _COMMIT.fullmatch(base_commit):
        raise ManifestValidationError("source.base_commit must be a real Git commit")
    source_commit = source["commit"]
    if status == "uncommitted_candidate":
        if source_commit is not None:
            raise ManifestValidationError("uncommitted_candidate requires source.commit = null")
    elif not isinstance(source_commit, str) or not _COMMIT.fullmatch(source_commit):
        raise ManifestValidationError("committed_release requires a real source.commit")
    return base_commit, source_commit if isinstance(source_commit, str) else None


def _validate_source_tree(source: Mapping[str, Any], base_commit: str, source_commit: str | None) -> str:
    source_tree = _string(source["tree"], "source.tree")
    if not _TREE.fullmatch(source_tree):
        raise ManifestValidationError("source.tree must be a real Git tree object id")
    if source_tree == base_commit:
        raise ManifestValidationError("source.tree must not substitute a Git commit id")
    if isinstance(source_commit, str) and source_tree == source_commit:
        raise ManifestValidationError("source.tree must not substitute source.commit")
    return source_tree


def _validate_source_expectations(
    source_tree: str,
    base_commit: str,
    expected_source_tree: str | None,
    expected_base_commit: str | None,
) -> None:
    if expected_source_tree is not None and source_tree != expected_source_tree:
        raise ManifestValidationError("source.tree does not match the isolated candidate tree")
    if expected_base_commit is not None and base_commit != expected_base_commit:
        raise ManifestValidationError("source.base_commit does not match the candidate base commit")


def _validate_source(document: Mapping[str, Any], expected_source_tree: str | None, expected_base_commit: str | None) -> None:
    source = _mapping(document["source"], _SOURCE_KEYS, "source")
    status = _string(source["status"], "source.status")
    if status not in _SOURCE_STATUSES:
        raise ManifestValidationError("unknown source provenance status")
    base_commit, source_commit = _validate_source_commits(source, status)
    source_tree = _validate_source_tree(source, base_commit, source_commit)
    _validate_source_expectations(source_tree, base_commit, expected_source_tree, expected_base_commit)


def _validate_application(document: Mapping[str, Any]) -> Mapping[str, Any]:
    application = _mapping(document["application"], _APPLICATION_KEYS, "application")
    if application["version"] != RELEASE_VERSION:
        raise ManifestValidationError("wrong application version")
    if application["wheel"] != APPLICATION_WHEEL:
        raise ManifestValidationError("application wheel name/version mismatch")
    _sha(application["wheel_sha256"], "application.wheel_sha256")
    return application


def _validate_payload(document: Mapping[str, Any]) -> Mapping[str, Any]:
    payload = _mapping(document["payload"], _PAYLOAD_KEYS, "payload")
    if bundle_relative_path(payload["path"], "payload.path") != PAYLOAD_ARCHIVE:
        raise ManifestValidationError(f"payload.path must be {PAYLOAD_ARCHIVE}")
    if bundle_relative_path(payload["inventory"], "payload.inventory") != PAYLOAD_INVENTORY:
        raise ManifestValidationError(f"payload.inventory must be {PAYLOAD_INVENTORY}")
    _sha(payload["sha256"], "payload.sha256")
    _sha(payload["inventory_sha256"], "payload.inventory_sha256")
    return payload


def _validate_artifacts(document: Mapping[str, Any], application: Mapping[str, Any]) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    runtime = _artifact(document["runtime_lock"], "runtime_lock", RUNTIME_LOCK)
    bootstrap = _artifact(document["bootstrap_pip_lock"], "bootstrap_pip_lock", BOOTSTRAP_PIP_LOCK)
    payload = document["payload"]
    paths = [application["wheel"], payload["path"], payload["inventory"], runtime["path"], bootstrap["path"]]
    if len(set(paths)) != len(paths):
        raise ManifestValidationError("duplicate logical artifact path")
    return runtime, bootstrap


def _validate_target(document: Mapping[str, Any]) -> None:
    if document["platform"] != PLATFORM:
        raise ManifestValidationError("wrong platform")
    if document["install_scope"] != INSTALL_SCOPE:
        raise ManifestValidationError("wrong install scope")


def _validate_build_tools(document: Mapping[str, Any]) -> None:
    tools = _mapping(document["build_tools"], _BUILD_TOOLS_KEYS, "build_tools")
    uv = _mapping(tools["uv"], _UV_KEYS, "build_tools.uv")
    if uv["version"] != UV_VERSION or uv["asset_url"] != UV_ASSET_URL or uv["sha256"] != UV_SHA256:
        raise ManifestValidationError("uv release-build pin differs from Phase 0")
    signature = _mapping(uv["expected_signature"], _SIGNATURE_KEYS, "build_tools.uv.expected_signature")
    if dict(signature) != UV_SIGNATURE:
        raise ManifestValidationError("uv signature evidence differs from Phase 0")
    pip = _mapping(tools["pip"], _PIP_KEYS, "build_tools.pip")
    if pip["version"] != BOOTSTRAP_PIP_VERSION or pip["wheel"] != BOOTSTRAP_PIP_WHEEL:
        raise ManifestValidationError("pip release-build pin differs from Phase 0")
    _sha(pip["wheel_sha256"], "build_tools.pip.wheel_sha256")
    if pip["wheel_sha256"] != BOOTSTRAP_PIP_SHA256:
        raise ManifestValidationError("pip bootstrap wheel hash differs from Phase 0")


def _validate_python(document: Mapping[str, Any]) -> None:
    python = _mapping(document["python"], _PYTHON_KEYS, "python")
    expected = {
        "implementation": PYTHON_IMPLEMENTATION,
        "exact_version": PYTHON_VERSION,
        "platform": PYTHON_PLATFORM,
        "build_identity": PYTHON_BUILD_IDENTITY,
        "build_date": PYTHON_BUILD_DATE,
        "source_artifact_url": PYTHON_SOURCE_ARTIFACT_URL,
        "source_artifact_sha256": PYTHON_SOURCE_ARTIFACT_SHA256,
    }
    if dict(python) != expected:
        raise ManifestValidationError("embedded CPython identity differs from Phase 0")


def _validate_candidate_id(document: Mapping[str, Any]) -> None:
    candidate = _string(document["candidate_id"], "candidate_id")
    if not _CANDIDATE.fullmatch(candidate) or candidate != candidate_id(document):
        raise ManifestValidationError("candidate_id is not the deterministic artifact identity")


def _validate_bundle_artifact(root: Path, label: str, artifact: Mapping[str, Any]) -> None:
    path = (root / artifact["path"]).resolve()
    if root not in path.parents or not path.is_file():
        raise ManifestValidationError(f"{label} is outside or missing from the bundle")
    if sha256_file(path) != artifact["sha256"]:
        raise ManifestValidationError(f"hash mismatch for {label}")


def _load_payload_inventory(root: Path, payload: Mapping[str, Any]) -> tuple[tuple[str, ...], dict[str, Any]]:
    inventory_path = root / payload["inventory"]
    try:
        inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
        validated = validate_inventory(inventory)
    except (OSError, json.JSONDecodeError, PayloadValidationError) as exc:
        raise ManifestValidationError(f"payload inventory is invalid: {inventory_path}") from exc
    expected = tuple(str(item["path"]) for item in validated["files"])
    by_path = {str(entry["path"]): entry for entry in validated["files"]}
    return expected, by_path


def _validate_payload_archive_entries(
    archive: zipfile.ZipFile,
    expected: tuple[str, ...],
    by_path: Mapping[str, Any],
) -> None:
    entries = archive.infolist()
    if any(item.is_dir() for item in entries):
        raise ManifestValidationError("payload archive must contain regular files only")
    for item in entries:
        unix_mode = (item.external_attr >> 16) & 0o170000
        if stat.S_ISLNK(unix_mode):
            raise ManifestValidationError("payload archive must not contain symlink members")
    actual = archive_member_paths(item.filename for item in entries)
    if actual != expected:
        raise ManifestValidationError("payload archive members differ from payload inventory")
    for item in entries:
        if item.file_size != by_path[item.filename]["size"]:
            raise ManifestValidationError(f"payload archive size differs from inventory: {item.filename}")
        digest = hashlib.sha256(archive.read(item.filename)).hexdigest()
        if digest != by_path[item.filename]["sha256"]:
            raise ManifestValidationError(f"payload archive hash differs from inventory: {item.filename}")


def _validate_payload_archive(root: Path, payload: Mapping[str, Any]) -> None:
    expected, by_path = _load_payload_inventory(root, payload)
    try:
        with zipfile.ZipFile(root / payload["path"]) as archive:
            _validate_payload_archive_entries(archive, expected, by_path)
    except (OSError, zipfile.BadZipFile, PayloadValidationError, ManifestValidationError) as exc:
        if isinstance(exc, ManifestValidationError):
            raise
        raise ManifestValidationError("payload archive is invalid") from exc


def _validate_bundle(
    bundle_root: Path,
    document: Mapping[str, Any],
    application: Mapping[str, Any],
    runtime: Mapping[str, Any],
    bootstrap: Mapping[str, Any],
    payload: Mapping[str, Any],
) -> None:
    root = bundle_root.resolve()
    if not root.is_dir():
        raise ManifestValidationError(f"bundle root is not a directory: {root}")
    entries = tuple(root.iterdir())
    if any(_is_linklike(path) for path in entries):
        raise ManifestValidationError("bundle contains a symlink/reparse member")
    members = tuple(sorted(path.name for path in entries if path.is_file()))
    if members != tuple(sorted(BUNDLE_MEMBERS)):
        raise ManifestValidationError(f"bundle member allowlist mismatch: {members}")
    if any(path.is_dir() for path in entries):
        raise ManifestValidationError("bundle contains an unexpected directory")
    artifacts = (
        ("application.wheel", {"path": application["wheel"], "sha256": application["wheel_sha256"]}),
        ("payload", payload),
        ("payload.inventory", {"path": payload["inventory"], "sha256": payload["inventory_sha256"]}),
        ("runtime_lock", runtime),
        ("bootstrap_pip_lock", bootstrap),
    )
    for label, artifact in artifacts:
        _validate_bundle_artifact(root, label, artifact)
    _validate_payload_archive(root, payload)


def validate_manifest(
    document: Mapping[str, Any],
    bundle_root: Path | None = None,
    *,
    expected_source_tree: str | None = None,
    expected_base_commit: str | None = None,
) -> Mapping[str, Any]:
    """Validate v003 identity and, optionally, all local bundle bytes."""

    document = _validate_document_shape(document)
    _validate_identity(document)
    _validate_source(document, expected_source_tree, expected_base_commit)
    application = _validate_application(document)
    payload = _validate_payload(document)
    runtime, bootstrap = _validate_artifacts(document, application)
    _validate_target(document)
    _validate_build_tools(document)
    _validate_python(document)
    _validate_candidate_id(document)
    if bundle_root is not None:
        _validate_bundle(bundle_root, document, application, runtime, bootstrap, payload)
    return document


def make_manifest(
    *,
    source_base_commit: str,
    source_tree: str,
    wheel_sha256: str,
    runtime_lock_sha256: str,
    bootstrap_pip_lock_sha256: str,
    payload_sha256: str = "4" * 64,
    payload_inventory_sha256: str = "5" * 64,
    source_status: str = "uncommitted_candidate",
    source_commit: str | None = None,
    uv_evidence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Construct the canonical v003 manifest shape."""

    uv = uv_evidence or {
        "version": UV_VERSION,
        "asset_url": UV_ASSET_URL,
        "sha256": UV_SHA256,
        "expected_signature": dict(UV_SIGNATURE),
    }
    document: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "product_identity": {
            "display_name": DISPLAY_NAME,
            "cli_name": CLI_NAME,
            "distribution_name": DISTRIBUTION_NAME,
            "application_namespace": APPLICATION_NAMESPACE,
            "identity_status": IDENTITY_STATUS,
        },
        "source": {
            "status": source_status,
            "base_commit": source_base_commit,
            "commit": source_commit,
            "tree": source_tree,
        },
        "application": {
            "version": RELEASE_VERSION,
            "wheel": APPLICATION_WHEEL,
            "wheel_sha256": wheel_sha256,
        },
        "payload": {
            "path": PAYLOAD_ARCHIVE,
            "sha256": payload_sha256,
            "inventory": PAYLOAD_INVENTORY,
            "inventory_sha256": payload_inventory_sha256,
        },
        "runtime_lock": {"path": RUNTIME_LOCK, "sha256": runtime_lock_sha256},
        "bootstrap_pip_lock": {"path": BOOTSTRAP_PIP_LOCK, "sha256": bootstrap_pip_lock_sha256},
        "platform": PLATFORM,
        "install_scope": INSTALL_SCOPE,
        "build_tools": {
            "uv": dict(uv),
            "pip": {
                "version": BOOTSTRAP_PIP_VERSION,
                "wheel": BOOTSTRAP_PIP_WHEEL,
                "wheel_sha256": BOOTSTRAP_PIP_SHA256,
            },
        },
        "python": {
            "implementation": PYTHON_IMPLEMENTATION,
            "exact_version": PYTHON_VERSION,
            "platform": PYTHON_PLATFORM,
            "build_identity": PYTHON_BUILD_IDENTITY,
            "build_date": PYTHON_BUILD_DATE,
            "source_artifact_url": PYTHON_SOURCE_ARTIFACT_URL,
            "source_artifact_sha256": PYTHON_SOURCE_ARTIFACT_SHA256,
        },
    }
    document["candidate_id"] = candidate_id(document)
    validate_manifest(document)
    return document


def render_manifest(document: Mapping[str, Any]) -> bytes:
    validate_manifest(document)
    return (json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


__all__ = [
    "ManifestValidationError",
    "bundle_relative_path",
    "candidate_id",
    "canonical_json_bytes",
    "make_manifest",
    "render_manifest",
    "sha256_bytes",
    "sha256_file",
    "validate_manifest",
]

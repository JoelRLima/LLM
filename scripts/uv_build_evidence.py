"""Validation for candidate-bound W18 uv build adversarial evidence."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from distribution.release_identity import UV_ASSET_URL, UV_SHA256, UV_VERSION

UV_EVIDENCE_SCHEMA = "W18-UV-BUILD-ADVERSARIAL-EVIDENCE-V2"
UV_REQUIRED_CASES = {
    "valid_pinned_artifact": "accepted",
    "wrong_hash": "rejected",
    "wrong_version": "rejected",
    "wrong_platform": "rejected",
    "authenticode": "rejected",
}


class UvBuildEvidenceError(ValueError):
    """Raised when uv build evidence is absent, stale, or malformed."""


def _read_document(path: Path) -> tuple[dict[str, Any], bytes]:
    try:
        evidence_bytes = path.read_bytes()
        document = json.loads(evidence_bytes.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise UvBuildEvidenceError(f"uv build evidence is unreadable: {path}") from exc
    if not isinstance(document, dict):
        raise UvBuildEvidenceError("uv build evidence must be a JSON object")
    return document, evidence_bytes


def _validate_identity(
    document: dict[str, Any],
    *,
    candidate_id: str,
    source_tree: str,
    release_manifest_sha256: str,
) -> None:
    expected = (candidate_id, source_tree, release_manifest_sha256)
    observed = (
        document.get("candidate_id"),
        document.get("source_tree"),
        document.get("release_manifest_sha256"),
    )
    if observed != expected:
        raise UvBuildEvidenceError("uv build evidence identity does not match the current candidate")
    if re.fullmatch(r"w18-[0-9a-f]{32}", candidate_id) is None:
        raise UvBuildEvidenceError("uv build evidence candidate ID is malformed")
    if re.fullmatch(r"[0-9a-f]{40}", source_tree) is None:
        raise UvBuildEvidenceError("uv build evidence source tree is malformed")
    if re.fullmatch(r"[0-9a-f]{64}", release_manifest_sha256) is None:
        raise UvBuildEvidenceError("uv build evidence manifest hash is malformed")


def _validate_pinned_uv(document: dict[str, Any]) -> None:
    pinned = document.get("pinned_uv")
    expected = {"url": UV_ASSET_URL, "archive_sha256": UV_SHA256, "version": UV_VERSION}
    if pinned != expected:
        raise UvBuildEvidenceError("uv build evidence does not describe the frozen pinned artifact")


def _validate_cases(document: dict[str, Any]) -> None:
    cases = document.get("cases")
    if not isinstance(cases, dict):
        raise UvBuildEvidenceError("uv build evidence cases are missing")
    for name, outcome in UV_REQUIRED_CASES.items():
        item = cases.get(name)
        if not isinstance(item, dict) or item.get("status") != "passed" or item.get("outcome") != outcome:
            raise UvBuildEvidenceError(f"uv build evidence case did not pass: {name}")


def validate_uv_build_evidence(
    path: Path,
    *,
    candidate_id: str,
    source_tree: str,
    release_manifest_sha256: str,
) -> tuple[dict[str, Any], bytes]:
    """Validate exact uv evidence against the current release identity."""

    document, evidence_bytes = _read_document(path)
    if document.get("schema_version") != UV_EVIDENCE_SCHEMA or document.get("status") != "passed":
        raise UvBuildEvidenceError("uv build evidence schema or status is invalid")
    _validate_identity(
        document,
        candidate_id=candidate_id,
        source_tree=source_tree,
        release_manifest_sha256=release_manifest_sha256,
    )
    _validate_pinned_uv(document)
    _validate_cases(document)
    return document, evidence_bytes


__all__ = [
    "UV_EVIDENCE_SCHEMA",
    "UV_REQUIRED_CASES",
    "UvBuildEvidenceError",
    "validate_uv_build_evidence",
]

"""Portable verification of immutable W23 evidence and tooling successors."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_WITNESS_KEYS = {
    "schema_version", "unit", "predecessor", "production_evidence",
    "historical_tooling_snapshots", "current_tooling",
    "historical_authority_bindings", "production_identity",
}
_IDENTITY_KEYS = {
    "modules", "edges", "largest_cross_domain_family",
    "total_cross_domain_family_membership", "f1_separated", "f2_modules",
    "production_fingerprint_sha256", "semantic_graph_sha256",
}
_K7_PATHS = {
    "distribution/candidate_manifest.py", "distribution/candidate_snapshot.py",
    "scripts/build_windows_payload.py", "scripts/run_wave18_adversarial.py",
    "tests/unit/test_candidate_manifest.py", "tests/unit/test_wave18_adversarial.py",
    "tests/unit/test_wave18_build_driver.py",
}
_K4_PATHS = {
    "scripts/w23_architecture/evidence.py", "tests/unit/test_w23_cycle_policy.py",
    "tests/unit/test_w23_evidence.py", "scripts/w23_acceptance.py",
    "tests/unit/test_w23_acceptance.py",
}


class EvidenceError(ValueError):
    """Explicit W23 evidence did not authenticate or satisfy its schema."""


def _object(value: Any, fields: set[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise EvidenceError(f"{label} has missing, unknown, or invalid fields")
    return value


def _path(value: Any, label: str) -> str:
    if (not isinstance(value, str) or not value or value.startswith("/")
            or "\\" in value or ":" in value
            or any(part in {"", ".", ".."} for part in value.split("/"))):
        raise EvidenceError(f"{label} must be a canonical repository-relative path")
    return value


def _digest(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise EvidenceError(f"{label} must be lowercase SHA-256")
    return value


def artifact_ref(value: Any) -> dict[str, str]:
    row = _object(value, {"path", "sha256"}, "ArtifactRef")
    return {"path": _path(row["path"], "ArtifactRef path"),
            "sha256": _digest(row["sha256"], "ArtifactRef digest")}


def authenticate_artifact(reference: Any, data: bytes) -> bytes:
    """Authenticate explicitly supplied bytes against an ArtifactRef."""
    ref = artifact_ref(reference)
    if not isinstance(data, bytes) or hashlib.sha256(data).hexdigest() != ref["sha256"]:
        raise EvidenceError(f"artifact digest mismatch: {ref['path']}")
    return data


def _json_bytes(data: bytes, label: str) -> Any:
    try:
        text = data.decode("utf-8")
        if text.startswith("\ufeff"):
            raise EvidenceError(f"{label} must not contain a BOM")
        return json.loads(text)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise EvidenceError(f"{label} is not valid UTF-8 JSON") from exc


def _sorted_rows(rows: Any, label: str) -> list[Mapping[str, Any]]:
    if not isinstance(rows, list):
        raise EvidenceError(f"{label} must be a list")
    checked: list[Mapping[str, Any]] = []
    paths = []
    for row in rows:
        item = _object(row, {"path", "sha256"}, label + " entry")
        path = _path(item["path"], label + " path")
        digest = _digest(item["sha256"], label + " digest")
        checked.append({"path": path, "sha256": digest})
        paths.append(path)
    if paths != sorted(set(paths)):
        raise EvidenceError(f"{label} paths must be sorted and unique")
    return checked


def _historical_snapshots(rows: Any) -> list[dict[str, str]]:
    if not isinstance(rows, list) or len(rows) != 1:
        raise EvidenceError("exactly one historical tooling snapshot is required")
    row = _object(rows[0], {"path", "original_path", "sha256"}, "historical snapshot")
    result = {"path": _path(row["path"], "snapshot path"),
              "original_path": _path(row["original_path"], "snapshot original path"),
              "sha256": _digest(row["sha256"], "snapshot digest")}
    return [result]


def _pointer_value(document: Any, pointer: str) -> Any:
    current = document
    for part in pointer[1:].split("/"):
        key = part.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict) or key not in current:
            raise EvidenceError("historical authority pointer does not resolve")
        current = current[key]
    return current


def _verify_historical_bindings(bindings: Any, predecessor: Any, production: Any) -> None:
    if not isinstance(bindings, list):
        raise EvidenceError("historical authority bindings must be a list")
    seen: set[tuple[str, str]] = set()
    docs = {
        "quality/w23/compatibility_closure_evidence.json": predecessor,
        "quality/w23/cycle_report_after_compatibility_retirement.json": production,
    }
    for binding in bindings:
        row = _object(binding, {"artifact", "pointer", "sha256"}, "historical authority binding")
        ref = artifact_ref(row["artifact"])
        pointer = row["pointer"]
        if not isinstance(pointer, str) or not pointer.startswith("/"):
            raise EvidenceError("historical authority binding pointer must be a JSON pointer")
        digest = _digest(row["sha256"], "historical authority digest")
        expected_ref = {
            "quality/w23/compatibility_closure_evidence.json": {
                "path": "quality/w23/compatibility_closure_evidence.json",
                "sha256": "ebb6f9dfc87e4033b3ea66ad907876a95e420507ec27135b2e8e59ea75044f9b",
            },
            "quality/w23/cycle_report_after_compatibility_retirement.json": {
                "path": "quality/w23/cycle_report_after_compatibility_retirement.json",
                "sha256": "ac8242d1d3cbbf065c7e9b041df05aa3b26cac86a0aeb86834126cb281f89ae1",
            },
        }.get(ref["path"])
        if expected_ref != ref or ref["path"] not in docs or not isinstance(docs[ref["path"]], dict):
            raise EvidenceError("historical authority binding references an unselected artifact")
        key = (ref["path"], pointer)
        if key in seen or _pointer_value(docs[ref["path"]], pointer) != digest:
            raise EvidenceError("historical authority binding is duplicate or inconsistent")
        seen.add(key)
    if not seen:
        raise EvidenceError("at least one historical authority binding is required")


def _verify_production_identity(identity: Any, production: Mapping[str, Any]) -> None:
    checked = _object(identity, _IDENTITY_KEYS, "production identity")
    current = production.get("current_evidence")
    if not isinstance(current, dict):
        raise EvidenceError("production evidence has no current identity")
    vector, f2 = current.get("vector"), current.get("f2_final_component")
    if not isinstance(vector, dict) or not isinstance(f2, dict):
        raise EvidenceError("production evidence has malformed F1/F2 data")
    expected = {
        "modules": 883, "edges": 3124,
        "largest_cross_domain_family": 4,
        "total_cross_domain_family_membership": 4,
        "f1_separated": current.get("f1_reconnected") is False,
        "f2_modules": f2.get("modules"),
        "production_fingerprint_sha256": current.get("production_fingerprint_sha256"),
        "semantic_graph_sha256": current.get("semantic_graph_sha256"),
    }
    if dict(checked) != expected or current.get("modules") != 883 or current.get("edges") != 3124:
        raise EvidenceError("production identity differs from accepted 883/3124/4/4 identity")
    if vector.get("largest_family_count") != 4 or vector.get("total_cross_domain_family_membership") != 4:
        raise EvidenceError("production cycle identity differs")
    if f2.get("unchanged") is not True:
        raise EvidenceError("F2 is not marked unchanged")


def _verify_snapshot(rows: Any, predecessor: Mapping[str, Any], supplied: Mapping[str, bytes]) -> None:
    snapshots = _historical_snapshots(rows)
    snapshot = snapshots[0]
    original = snapshot["original_path"]
    recorded = predecessor.get("current_tooling_sha256")
    if not isinstance(recorded, dict) or recorded.get(original) != snapshot["sha256"]:
        raise EvidenceError("historical snapshot does not authenticate predecessor tooling")
    data = supplied.get(snapshot["path"])
    if data is None or hashlib.sha256(data).hexdigest() != snapshot["sha256"]:
        raise EvidenceError("historical snapshot bytes are missing or stale")


def _verify_current_tooling(rows: Any, supplied: Mapping[str, bytes], old_hashes: Mapping[str, Any], old_path: str) -> None:
    current_rows = _sorted_rows(rows, "current tooling")
    expected_paths = {item["path"] for item in current_rows}
    expected = (set(old_hashes) - {old_path}) | _K7_PATHS | _K4_PATHS
    if expected_paths != expected:
        raise EvidenceError("current tooling set is incomplete or unexpected")
    if set(supplied) != expected_paths:
        raise EvidenceError("current tooling membership differs from witness")
    for item in current_rows:
        data = supplied[item["path"]]
        if not isinstance(data, bytes) or hashlib.sha256(data).hexdigest() != item["sha256"]:
            raise EvidenceError(f"current tooling bytes are stale: {item['path']}")


def _verify_header(row: Mapping[str, Any]) -> None:
    if type(row["schema_version"]) is not int or row["schema_version"] != 1:
        raise EvidenceError("unsupported tooling witness schema")
    if row["unit"] != "W23_ACCEPTANCE_TOOLING_SUCCESSOR":
        raise EvidenceError("unexpected tooling witness unit")


def _authenticate_selected_artifacts(
    row: Mapping[str, Any], predecessor_bytes: bytes, production_bytes: bytes,
) -> tuple[dict[str, Any], dict[str, Any]]:
    predecessor_ref = artifact_ref(row["predecessor"])
    production_ref = artifact_ref(row["production_evidence"])
    expected_refs = {
        "predecessor": ("quality/w23/compatibility_closure_evidence.json",
                        "ebb6f9dfc87e4033b3ea66ad907876a95e420507ec27135b2e8e59ea75044f9b"),
        "production_evidence": ("quality/w23/cycle_report_after_compatibility_retirement.json",
                                 "ac8242d1d3cbbf065c7e9b041df05aa3b26cac86a0aeb86834126cb281f89ae1"),
    }
    for name, ref in (("predecessor", predecessor_ref), ("production_evidence", production_ref)):
        if (ref["path"], ref["sha256"]) != expected_refs[name]:
            raise EvidenceError(f"unexpected or stale {name} selection")
    predecessor = _json_bytes(authenticate_artifact(predecessor_ref, predecessor_bytes), "predecessor")
    production = _json_bytes(authenticate_artifact(production_ref, production_bytes), "production evidence")
    if not isinstance(predecessor, dict) or predecessor.get("schema_version") != 1:
        raise EvidenceError("invalid predecessor evidence")
    if not isinstance(production, dict) or production.get("verdict") != "PASS":
        raise EvidenceError("production successor is not accepted")
    return predecessor, production


def verify_witness(
    witness_bytes: bytes,
    predecessor_bytes: bytes,
    production_bytes: bytes,
    tooling_bytes: Mapping[str, bytes],
    historical_snapshot_bytes: Mapping[str, bytes],
) -> dict[str, Any]:
    """Verify a fixed witness using only explicitly supplied portable inputs."""
    witness = _json_bytes(witness_bytes, "tooling witness")
    canonical = (json.dumps(witness, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    if canonical != witness_bytes:
        raise EvidenceError("tooling witness is not canonical JSON")
    row = _object(witness, _WITNESS_KEYS, "tooling witness")
    _verify_header(row)
    predecessor, production = _authenticate_selected_artifacts(row, predecessor_bytes, production_bytes)
    _verify_snapshot(row["historical_tooling_snapshots"], predecessor, historical_snapshot_bytes)
    snapshots = row["historical_tooling_snapshots"]
    if (not snapshots or snapshots[0].get("path")
            != "quality/w23/historical_tooling/test_w23_cycle_policy.pre_k4.txt"):
        raise EvidenceError("unexpected historical snapshot")
    if snapshots[0].get("original_path") != "tests/unit/test_w23_cycle_policy.py":
        raise EvidenceError("historical snapshot source path differs")
    _verify_production_identity(row["production_identity"], production)
    old_hashes = predecessor.get("current_tooling_sha256")
    if not isinstance(old_hashes, dict):
        raise EvidenceError("predecessor has no historical tooling bindings")
    _verify_current_tooling(row["current_tooling"], tooling_bytes, old_hashes,
                            "tests/unit/test_w23_cycle_policy.py")
    _verify_historical_bindings(row["historical_authority_bindings"], predecessor, production)
    return dict(row)


def verify_historical_binding(binding: Mapping[str, Any], artifact_bytes: bytes) -> str:
    """Authenticate a recorded historical digest; this grants no current authority."""
    row = _object(binding, {"artifact", "pointer", "sha256"}, "historical authority binding")
    artifact = artifact_ref(row["artifact"])
    document = _json_bytes(authenticate_artifact(artifact, artifact_bytes), "historical authority artifact")
    pointer = row["pointer"]
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        raise EvidenceError("historical authority binding pointer must be a JSON pointer")
    digest = _digest(row["sha256"], "historical authority digest")
    if _pointer_value(document, pointer) != digest:
        raise EvidenceError("historical binding differs from recorded evidence")
    return "HISTORICAL_ONLY"

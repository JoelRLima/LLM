"""Closed, exact-byte repository-candidate manifests.

This module validates identity descriptions. It never decides that a manifest
is accepted: callers must supply its expected digest through a trusted
selection channel.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping
from typing import Any, Callable

SCHEMA_VERSION = 1
MANIFEST_KIND = "EXACT_BYTE_REPOSITORY_CANDIDATE"
_HEX = re.compile(r"^[0-9a-f]+$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
_MODES = {"100644", "100755"}


class CandidateManifestError(ValueError):
    """A candidate description is malformed or inconsistent."""


def _object(value: Any, keys: set[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise CandidateManifestError(f"{label} has missing, unknown, or invalid fields")
    return value


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or _CONTROL.search(value):
        raise CandidateManifestError(f"{label} must be nonempty control-free text")
    return value


def validate_repo_path(value: Any) -> str:
    """Validate a portable repository-relative POSIX path before I/O."""
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise CandidateManifestError("path is not canonical repository-relative POSIX text")
    if value.startswith("/") or value.startswith("//") or _CONTROL.search(value):
        raise CandidateManifestError("absolute, UNC, or control-character path")
    if unicodedata.normalize("NFC", value) != value:
        raise CandidateManifestError("path is not Unicode NFC normalized")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise CandidateManifestError("path contains an empty, dot, or traversal component")
    for part in parts:
        if part.endswith((".", " ")) or any(char in part for char in '<>"|?*'):
            raise CandidateManifestError("path is not safe on Windows filesystems")
        if part.casefold() == ".git":
            raise CandidateManifestError("Git administration paths cannot be candidate files")
        stem = part.split(".", 1)[0].upper()
        if stem in _WINDOWS_RESERVED or stem.rstrip("¹²³") in _WINDOWS_RESERVED:
            raise CandidateManifestError("path contains a reserved Windows name")
    return value


def _sha256(value: Any, label: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or not _HEX.fullmatch(value):
        raise CandidateManifestError(f"{label} must be lowercase SHA-256")
    return value


def _git_oid(value: Any, object_format: str, label: str) -> str:
    length = {"sha1": 40, "sha256": 64}.get(object_format)
    if length is None or not isinstance(value, str) or len(value) != length or not _HEX.fullmatch(value):
        raise CandidateManifestError(f"{label} is not a valid {object_format} Git object id")
    return value


def _sorted_unique_paths(rows: Any, label: str, row_validator: Any) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        raise CandidateManifestError(f"{label} must be a list")
    result: list[dict[str, Any]] = []
    paths: list[str] = []
    folded: set[str] = set()
    for row in rows:
        checked = row_validator(row)
        path = checked["path"]
        paths.append(path)
        if path.casefold() in folded:
            raise CandidateManifestError(f"case-folded path collision in {label}: {path}")
        folded.add(path.casefold())
        result.append(checked)
    if paths != sorted(paths) or len(paths) != len(set(paths)):
        raise CandidateManifestError(f"{label} paths must be strictly sorted and unique")
    return result


def _file(value: Any) -> dict[str, Any]:
    row = _object(value, {"path", "mode", "bytes", "sha256"}, "candidate file")
    path = validate_repo_path(row["path"])
    if not isinstance(row["mode"], str) or row["mode"] not in _MODES:
        raise CandidateManifestError(f"unsupported mode for {path}")
    if type(row["bytes"]) is not int or row["bytes"] < 0:
        raise CandidateManifestError(f"invalid byte length for {path}")
    return {"path": path, "mode": row["mode"], "bytes": row["bytes"], "sha256": _sha256(row["sha256"], path)}


def _artifact(value: Any, label: str) -> dict[str, str]:
    row = _object(value, {"path", "sha256"}, label)
    return {"path": validate_repo_path(row["path"]), "sha256": _sha256(row["sha256"], label)}


def _base_entry(value: Any, object_format: str) -> dict[str, str]:
    row = _object(value, {"mode", "blob"}, "overlay before entry")
    if not isinstance(row["mode"], str) or row["mode"] not in _MODES:
        raise CandidateManifestError("unsupported base mode")
    return {"mode": row["mode"], "blob": _git_oid(row["blob"], object_format, "overlay base blob")}


def _overlay(value: Any, object_format: str) -> dict[str, Any]:
    row = _object(value, {"path", "operation", "before", "after_sha256", "acceptance_record_id"}, "overlay entry")
    path = validate_repo_path(row["path"])
    operation = row["operation"]
    before = None if row["before"] is None else _base_entry(row["before"], object_format)
    after = None if row["after_sha256"] is None else _sha256(row["after_sha256"], f"overlay {path}")
    valid = ((operation == "add" and before is None and after is not None)
             or (operation == "delete" and before is not None and after is None)
             or (operation == "modify" and before is not None and after is not None))
    if not valid:
        raise CandidateManifestError(f"invalid overlay operation fields for {path}")
    return {"path": path, "operation": operation, "before": before, "after_sha256": after,
            "acceptance_record_id": _text(row["acceptance_record_id"], "acceptance_record_id")}


def _workspace(value: Any) -> dict[str, Any]:
    row = _object(value, {"protected_files", "operational_roots"}, "workspace_context")
    protected = _sorted_unique_paths(row["protected_files"], "protected_files", lambda item: _file(item))
    roots = row["operational_roots"]
    if not isinstance(roots, list):
        raise CandidateManifestError("operational_roots must be a list")
    checked_roots = [validate_repo_path(item) for item in roots]
    if checked_roots != sorted(checked_roots) or len(checked_roots) != len(set(checked_roots)):
        raise CandidateManifestError("operational_roots must be sorted and unique")
    folded = [path.casefold() for path in checked_roots]
    if len(folded) != len(set(folded)):
        raise CandidateManifestError("operational_roots contain a case-folded collision")
    for file in protected:
        if any(file["path"] == root or file["path"].startswith(root + "/") for root in checked_roots):
            raise CandidateManifestError("protected file overlaps an operational root")
    return {"protected_files": protected, "operational_roots": checked_roots}


def _validate_header(row: Mapping[str, Any]) -> tuple[str, str, dict[str, str], str]:
    if type(row["schema_version"]) is not int or row["schema_version"] != SCHEMA_VERSION:
        raise CandidateManifestError("unsupported schema_version")
    if row["kind"] != MANIFEST_KIND:
        raise CandidateManifestError("unsupported manifest kind")
    unit = _text(row["unit"], "unit")
    object_format = _text(row["object_format"], "object_format")
    if object_format not in {"sha1", "sha256"}:
        raise CandidateManifestError("unsupported Git object format")
    base = _object(row["base"], {"head", "tree"}, "base")
    checked_base = {"head": _git_oid(base["head"], object_format, "base head"),
                    "tree": _git_oid(base["tree"], object_format, "base tree")}
    tree = _git_oid(row["source_tree"], object_format, "source_tree")
    return unit, object_format, checked_base, tree


def _lineage(value: Any) -> dict[str, Any]:
    lineage = _object(value, {"authority_predecessor", "inherited_observation", "acceptance_records"}, "lineage")
    authority = _artifact(lineage["authority_predecessor"], "authority_predecessor")
    inherited = _artifact(lineage["inherited_observation"], "inherited_observation")
    records = lineage["acceptance_records"]
    if not isinstance(records, list):
        raise CandidateManifestError("acceptance_records must be a list")
    checked_records: list[dict[str, Any]] = []
    identifiers: list[str] = []
    for record in records:
        item = _object(record, {"id", "decision", "evidence"}, "acceptance record")
        identifier = _text(item["id"], "acceptance record id")
        decision = _artifact(item["decision"], "acceptance decision")
        evidence = item["evidence"]
        if not isinstance(evidence, list):
            raise CandidateManifestError("acceptance evidence must be a list")
        checked_evidence = [_artifact(entry, "acceptance evidence") for entry in evidence]
        evidence_paths = [entry["path"] for entry in checked_evidence]
        if evidence_paths != sorted(evidence_paths) or len(evidence_paths) != len(set(evidence_paths)):
            raise CandidateManifestError("acceptance evidence must be sorted and unique")
        checked_records.append({"id": identifier, "decision": decision, "evidence": checked_evidence})
        identifiers.append(identifier)
    if identifiers != sorted(identifiers) or len(identifiers) != len(set(identifiers)):
        raise CandidateManifestError("acceptance record IDs must be sorted and unique")
    return {"authority_predecessor": authority, "inherited_observation": inherited,
            "acceptance_records": checked_records}


def _selected(value: Any, files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    evidence = value
    if not isinstance(evidence, list):
        raise CandidateManifestError("selected_evidence must be a list")
    checked_selected: list[dict[str, str]] = []
    roles: list[str] = []
    for entry in evidence:
        item = _object(entry, {"role", "artifact"}, "selected evidence")
        role = _text(item["role"], "evidence role")
        checked_selected.append({"role": role, "artifact": _artifact(item["artifact"], "selected evidence artifact")})
        roles.append(role)
    if roles != sorted(roles) or len(roles) != len(set(roles)):
        raise CandidateManifestError("selected evidence roles must be sorted and unique")
    file_by_path = {item["path"]: item for item in files}
    for item in checked_selected:
        source = file_by_path.get(item["artifact"]["path"])
        if source is None or source["sha256"] != item["artifact"]["sha256"]:
            raise CandidateManifestError("selected versioned evidence must be an exact candidate file")
    return checked_selected


def _validate_cross_references(
    files: list[dict[str, Any]], overlay: list[dict[str, Any]], lineage: Mapping[str, Any],
    workspace: Mapping[str, Any],
) -> None:
    identifiers = {record["id"] for record in lineage["acceptance_records"]}
    if {entry["acceptance_record_id"] for entry in overlay} - identifiers:
        raise CandidateManifestError("overlay references an undeclared acceptance record")
    file_by_path = {item["path"]: item for item in files}
    overlay_by_path = {item["path"]: item for item in overlay}
    for path, item in overlay_by_path.items():
        file = file_by_path.get(path)
        if item["operation"] == "delete":
            if file is not None:
                raise CandidateManifestError(f"deleted path remains in files: {path}")
        elif file is None or item["after_sha256"] != file["sha256"]:
            raise CandidateManifestError(f"overlay and complete file manifest differ: {path}")
    protected_paths = {item["path"] for item in workspace["protected_files"]}
    operational = workspace["operational_roots"]
    for path in file_by_path:
        if path in protected_paths or any(path == root or path.startswith(root + "/") for root in operational):
            raise CandidateManifestError(f"candidate source overlaps workspace context: {path}")
    for path in protected_paths:
        if path in file_by_path:
            raise CandidateManifestError(f"candidate source overlaps protected file: {path}")


def parse_base_tree(raw: bytes, object_format: str) -> dict[str, dict[str, str]]:
    """Parse supported entries from `git ls-tree -r -z` output."""
    result: dict[str, dict[str, str]] = {}
    for record in raw.split(b"\0"):
        if not record:
            continue
        header, separator, path_bytes = record.partition(b"\t")
        if not separator:
            raise CandidateManifestError("malformed base-tree entry")
        try:
            fields = header.decode("ascii").split()
        except UnicodeError as exc:
            raise CandidateManifestError("base tree entry header is not ASCII") from exc
        if len(fields) != 3 or fields[1] != "blob" or fields[0] not in _MODES:
            raise CandidateManifestError("base tree contains an unsupported file type")
        try:
            path = validate_repo_path(path_bytes.decode("utf-8"))
        except (UnicodeError, CandidateManifestError) as exc:
            raise CandidateManifestError("base tree contains an unsupported path") from exc
        result[path] = {"mode": fields[0], "blob": _git_oid(fields[2], object_format, "base blob")}
    return result


def validate_overlay_against_base(
    manifest: Mapping[str, Any], base: Mapping[str, Mapping[str, str]], read_blob: Callable[[str], bytes],
) -> None:
    """Require the overlay to describe the exact complete base-to-source difference."""
    files = {row["path"]: row for row in manifest["files"]}
    changed: dict[str, dict[str, Any]] = {}
    for path in set(base) | set(files):
        before, after = base.get(path), files.get(path)
        digest = None if before is None else hashlib.sha256(read_blob(before["blob"])).hexdigest()
        operation = "add" if before is None else "delete" if after is None else "modify"
        if before is not None and after is not None and before["mode"] == after["mode"] and digest == after["sha256"]:
            continue
        changed[path] = {"operation": operation, "before": before,
                         "after_sha256": None if after is None else after["sha256"]}
    listed = {row["path"]: row for row in manifest["overlay"]}
    if set(changed) != set(listed):
        raise CandidateManifestError("overlay path set is not the exact base-to-candidate difference")
    for path, expected in changed.items():
        if any(listed[path][key] != expected[key] for key in expected):
            raise CandidateManifestError(f"overlay differs from base and complete manifest: {path}")


def validate_git_overlay(
    manifest: Mapping[str, Any], raw_tree: bytes, read_blob: Callable[[str], bytes],
) -> None:
    """Validate an overlay against a parsed Git tree and its raw blobs."""
    base = parse_base_tree(raw_tree, manifest["object_format"])
    validate_overlay_against_base(manifest, base, read_blob)


def manifest_references(manifest: Mapping[str, Any], *, workspace: bool) -> list[Mapping[str, str]]:
    """Return exactly the evidence references declared by the selected manifest."""
    references: list[Mapping[str, str]] = []
    if workspace:
        lineage = manifest["lineage"]
        references.extend((lineage["authority_predecessor"], lineage["inherited_observation"]))
        for record in lineage["acceptance_records"]:
            references.append(record["decision"])
            references.extend(record["evidence"])
    references.extend(item["artifact"] for item in manifest["selected_evidence"])
    return references


def validate_manifest(value: Any) -> dict[str, Any]:
    """Validate and normalize each independent identity collection."""
    keys = {"schema_version", "kind", "unit", "object_format", "base", "source_tree", "files",
            "overlay", "lineage", "selected_evidence", "workspace_context"}
    row = _object(value, keys, "manifest")
    unit, object_format, base, tree = _validate_header(row)
    files = _sorted_unique_paths(row["files"], "files", _file)
    overlay = _sorted_unique_paths(row["overlay"], "overlay", lambda item: _overlay(item, object_format))
    lineage = _lineage(row["lineage"])
    selected = _selected(row["selected_evidence"], files)
    workspace = _workspace(row["workspace_context"])
    _validate_cross_references(files, overlay, lineage, workspace)
    return {"schema_version": SCHEMA_VERSION, "kind": MANIFEST_KIND, "unit": unit,
            "object_format": object_format, "base": base, "source_tree": tree, "files": files,
            "overlay": overlay, "lineage": lineage, "selected_evidence": selected, "workspace_context": workspace}


def canonical_manifest_bytes(value: Any) -> bytes:
    """Serialize a validated manifest in the specified UTF-8 canonical form."""
    checked = validate_manifest(value)
    return (json.dumps(checked, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def parse_manifest(data: bytes, expected_sha256: str) -> dict[str, Any]:
    """Authenticate bytes before parsing; reject duplicate keys and noncanonical JSON."""
    _sha256(expected_sha256, "expected manifest digest")
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected_sha256:
        raise CandidateManifestError("manifest digest differs from caller-supplied selection")
    try:
        text = data.decode("utf-8")
        if text.startswith("\ufeff"):
            raise CandidateManifestError("manifest must not contain a UTF-8 BOM")

        def unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in pairs:
                if key in result:
                    raise CandidateManifestError(f"duplicate JSON key: {key}")
                result[key] = value
            return result

        parsed = json.loads(text, object_pairs_hook=unique_pairs,
                            parse_constant=lambda value: (_ for _ in ()).throw(CandidateManifestError(f"invalid number: {value}")),
                            parse_float=lambda _value: (_ for _ in ()).throw(CandidateManifestError("floats are not permitted")))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise CandidateManifestError("manifest is not valid UTF-8 JSON") from exc
    checked = validate_manifest(parsed)
    if canonical_manifest_bytes(checked) != data:
        raise CandidateManifestError("manifest JSON is not in canonical serialization")
    return checked


__all__ = ["CandidateManifestError", "MANIFEST_KIND", "SCHEMA_VERSION", "canonical_manifest_bytes",
           "parse_manifest", "validate_manifest", "validate_repo_path"]

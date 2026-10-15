"""Validate a detached, inactive W23 successor without granting selection.

The legacy v007 acceptance entry point is intentionally unchanged. This entry
point authenticates an independently pinned prospective contract and candidate;
it cannot activate authority, mutate the selector, or convert old pins to PASS.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Any

from distribution.candidate_manifest import manifest_references, parse_manifest, validate_repo_path
from scripts.w23_tooling_successor import verify_successor

PREDECESSOR_SHA256 = "418e0ccac42c9532ff9519f72631045dce82a3b370f6455a08f42fdecfba94e4"
HISTORICAL_INDEX_PIN = "be38e8c7005814cd0462bdd2933fdf5207a62e5b9932c80eff2ea56d3ebeaf8f"
REQUIRED_GATES = frozenset({"ruff", "quality_c901", "mypy_linux", "mypy_win32", "full_pytest",
                            "installed_wheel", "w18_lifecycle", "remote_ci", "windows_symlinks"})
GRANTS = {"selection": False, "activation": False, "merge": False, "publication": False}


class AuthorityError(ValueError):
    """A prospective authority failed authentication or semantic checks."""


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _object(data: bytes, expected: str) -> dict[str, Any]:
    if digest(data) != expected:
        raise AuthorityError("independently expected digest differs")
    value = json.loads(data.decode("utf-8"))
    if not isinstance(value, dict):
        raise AuthorityError("authority artifact must be an object")
    return value


def _regular(root: Path, relative: str) -> Path:
    validate_repo_path(relative)
    current = root
    for component in relative.split("/"):
        current /= component
        info = current.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise AuthorityError("symlink/reparse artifact rejected")
    if not stat.S_ISREG(current.lstat().st_mode):
        raise AuthorityError("non-regular artifact rejected")
    return current


def _reference(root: Path, row: dict[str, str]) -> bytes:
    if set(row) != {"path", "sha256"}:
        raise AuthorityError("artifact reference schema differs")
    data = _regular(root, row["path"]).read_bytes()
    if digest(data) != row["sha256"]:
        raise AuthorityError("artifact digest differs: " + row["path"])
    return data


def _semantics(proposal: dict[str, Any]) -> None:
    fields = {"schema", "version", "state", "grants", "predecessor", "candidate", "contract", "spec",
              "verifier", "tooling_record", "historical_index", "bindings", "gates"}
    if set(proposal) != fields:
        raise AuthorityError("prospective schema differs")
    if (proposal["schema"] != "W23_PROSPECTIVE_AUTHORITY" or proposal["version"] != 10
            or proposal["state"] != "PROPOSED_INACTIVE" or proposal["grants"] != GRANTS
            or any(value is not False for value in proposal["grants"].values())):
        raise AuthorityError("inactive proposal and denied grants required")
    index = proposal["historical_index"]
    if index != {"pin": HISTORICAL_INDEX_PIN, "state": "UNVERIFIED_HISTORICAL_DIVERGENCE",
                 "policy": "prospective_identity_does_not_repair_historical_raw_pin"}:
        raise AuthorityError("historical index finding was reinterpreted")


def _predecessor(bundle: Path, proposal: dict[str, Any]) -> None:
    ref = proposal["predecessor"]
    if ref["sha256"] != PREDECESSOR_SHA256:
        raise AuthorityError("v009 predecessor differs")
    predecessor = _object(_reference(bundle, ref), PREDECESSOR_SHA256)
    # Hash identity, not a declaration that v009's historical raw-index pin passes.
    if predecessor.get("schema_version") != 1:
        raise AuthorityError("predecessor schema differs")


def _tree(node: dict[str, Any], algorithm: str) -> bytes:
    records = []
    for name, value in node.items():
        if isinstance(value, dict):
            mode, oid, key = "40000", _tree(value, algorithm), name.encode() + b"/"
        else:
            mode, oid = value
            key = name.encode()
        records.append((key, mode.encode() + b" " + name.encode() + b"\0" + oid))
    body = b"".join(row for _, row in sorted(records))
    return hashlib.new(algorithm, b"tree " + str(len(body)).encode() + b"\0" + body).digest()


def _source_paths(root: Path, ignored: set[str], prefix: str = "") -> set[str]:
    result: set[str] = set()
    for entry in os.scandir(root):
        relative = prefix + entry.name
        if relative in ignored:
            continue
        info = entry.stat(follow_symlinks=False)
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise AuthorityError("source symlink/reparse rejected")
        if stat.S_ISDIR(info.st_mode):
            result.update(_source_paths(Path(entry.path), ignored, relative + "/"))
        elif stat.S_ISREG(info.st_mode):
            result.add(relative)
        else:
            raise AuthorityError("non-regular source entry")
    return result


def _candidate(source: Path, manifest: dict[str, Any]) -> None:
    ignored = set(manifest["workspace_context"]["operational_roots"]) | {".git"}
    if _source_paths(source, ignored) != {row["path"] for row in manifest["files"]}:
        raise AuthorityError("complete source membership differs")
    node: dict[str, Any] = {}
    for row in manifest["files"]:
        path = _regular(source, row["path"])
        data = path.read_bytes()
        if os.name != "nt" and bool(path.stat().st_mode & 0o111) != (row["mode"] == "100755"):
            raise AuthorityError("candidate executable mode differs")
        if len(data) != row["bytes"] or digest(data) != row["sha256"]:
            raise AuthorityError("candidate file differs: " + row["path"])
        tree = node
        components = row["path"].split("/")
        for component in components[:-1]:
            tree = tree.setdefault(component, {})
        oid = hashlib.new(manifest["object_format"], b"blob " + str(len(data)).encode() + b"\0" + data).digest()
        tree[components[-1]] = (row["mode"], oid)
    if _tree(node, manifest["object_format"]).hex() != manifest["source_tree"]:
        raise AuthorityError("candidate source tree differs")


def _required_artifacts(bundle: Path, proposal: dict[str, Any]) -> set[str]:
    expected = {proposal[key]["path"] for key in ("predecessor", "candidate", "contract", "spec", "tooling_record")}
    manifest = json.loads(_reference(bundle, proposal["candidate"]))
    expected.update(row["path"] for row in manifest_references(manifest, workspace=True))
    for row in proposal["gates"].values():
        expected.add(row["evidence"]["path"])
        observation = json.loads(_reference(bundle, row["evidence"]))
        expected.update(ref["path"] for ref in observation.get("evidence_refs", []))
    return expected


def _bindings(bundle: Path, proposal: dict[str, Any]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in proposal["bindings"]:
        if set(row) != {"source", "destination", "sha256"}:
            raise AuthorityError("binding schema differs")
        destination = validate_repo_path(row["destination"])
        if not destination.startswith("quality/w23/authority/v010-cand03/") or destination in seen:
            raise AuthorityError("binding escapes immutable successor namespace or duplicates destination")
        seen.add(destination)
        _reference(bundle, {"path": row["source"], "sha256": row["sha256"]})
        result.append(dict(row))
    expected = _required_artifacts(bundle, proposal)
    if {row["source"] for row in result} != expected or len(result) != len(expected):
        raise AuthorityError("binding membership is incomplete")
    return result


def _gate_observation(bundle: Path, reference: dict[str, str], name: str, state: str, tree: str) -> None:
    data = _reference(bundle, reference)
    observation = json.loads(data)
    if (observation.get("schema") != "W23_GATE_OBSERVATION" or observation.get("gate") != name
            or observation.get("state") != state or observation.get("source_tree") != tree):
        raise AuthorityError("gate observation is not bound to its declared identity/state")
    if state == "OBSERVED PASS":
        if type(observation.get("exit_code")) is not int or observation["exit_code"] != 0:
            raise AuthorityError("passing gate has no successful observed exit")
        if observation.get("execution") not in {"NATIVE_LOCAL", "REMOTE_CI"} or not observation.get("evidence_refs"):
            raise AuthorityError("passing gate lacks real execution evidence")
    for ref in observation.get("evidence_refs", []):
        _reference(bundle, ref)


def _gates(bundle: Path, proposal: dict[str, Any], tree: str) -> bool:
    gates = proposal["gates"]
    if not isinstance(gates, dict) or set(gates) != REQUIRED_GATES:
        raise AuthorityError("mandatory gate membership differs")
    passed = True
    for name, row in gates.items():
        if set(row) != {"state", "source_tree", "evidence"} or row["source_tree"] != tree:
            raise AuthorityError("gate identity differs")
        if row["state"] not in {"OBSERVED PASS", "OBSERVED FAIL", "UNKNOWN", "SKIPPED"}:
            raise AuthorityError("gate cannot be simulated into acceptance")
        _gate_observation(bundle, row["evidence"], name, row["state"], tree)
        passed = passed and row["state"] == "OBSERVED PASS"
    return passed


def verify_prospective(source: Path, bundle: Path, proposal_bytes: bytes, expected_sha256: str) -> dict[str, Any]:
    """Verify current source and archived evidence separately; never grant authority."""
    proposal = _object(proposal_bytes, expected_sha256)
    _semantics(proposal)
    _predecessor(bundle, proposal)
    _reference(bundle, proposal["contract"])
    _reference(bundle, proposal["spec"])
    verifier = proposal["verifier"]
    if verifier["path"] != "scripts/w23_authority.py":
        raise AuthorityError("canonical prospective verifier path differs")
    _reference(source, verifier)
    raw = _reference(bundle, proposal["candidate"])
    manifest = parse_manifest(raw, proposal["candidate"]["sha256"])
    if manifest["lineage"]["authority_predecessor"] != proposal["predecessor"]:
        raise AuthorityError("candidate lineage does not recognize the exact predecessor")
    for reference in manifest_references(manifest, workspace=True):
        _reference(bundle, reference)
    _candidate(source, manifest)
    record = _reference(bundle, proposal["tooling_record"])
    tooling = verify_successor(source, record, proposal["tooling_record"]["sha256"])
    bindings = _bindings(bundle, proposal)
    gates_passed = _gates(bundle, proposal, manifest["source_tree"])
    return {"status": "PROSPECTIVE_VERIFIED_INACTIVE", "source_tree": manifest["source_tree"],
            "historical_integrity": tooling["historical_integrity"], "current_identity": tooling["current_identity"],
            "historical_raw_index": "UNVERIFIED_HISTORICAL_DIVERGENCE", "bindings": bindings,
            "mandatory_gates_passed": gates_passed, "owner_selection": "PENDING", "grants": dict(GRANTS),
            "canonical_acceptance": "NOT_GRANTED", "green": False}


def verify_owner_approval(source: Path, bundle: Path, proposal_bytes: bytes, proposal_sha256: str,
                          approval_bytes: bytes, independently_approved_sha256: str) -> dict[str, Any]:
    """Verify a future separately approved owner record, without installing it.

    The expected approval digest is an external owner trust root; callers must
    never discover it from the record itself. No approval is shipped by default.
    """
    result = verify_prospective(source, bundle, proposal_bytes, proposal_sha256)
    approval = _object(approval_bytes, independently_approved_sha256)
    expected = {"schema": "W23_OWNER_SELECTION_APPROVAL", "proposal_sha256": proposal_sha256,
                "source_tree": result["source_tree"], "predecessor_sha256": PREDECESSOR_SHA256,
                "decision": "APPROVE_CANONICAL_SELECTION", "activation_authorized": False,
                "publication_authorized": False, "merge_authorized": False}
    if approval != expected or any(approval[key] is not False for key in
            ("activation_authorized", "publication_authorized", "merge_authorized")):
        raise AuthorityError("owner approval is not for these exact bytes and limited scope")
    if not result["mandatory_gates_passed"]:
        raise AuthorityError("owner approval cannot waive mandatory acceptance gates")
    return {**result, "canonical_acceptance": "OWNER_APPROVED_BYTES_AND_GATES_VERIFIED",
            "owner_selection": "APPROVED_EXTERNALLY", "activation_authorized": False, "green": False}


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--proposal", required=True)
    parser.add_argument("--sha256", required=True)
    args = parser.parse_args()
    raw = _regular(args.bundle, args.proposal).read_bytes()
    print(json.dumps(verify_prospective(args.source, args.bundle, raw, args.sha256), sort_keys=True, indent=2))


if __name__ == "__main__":
    main()

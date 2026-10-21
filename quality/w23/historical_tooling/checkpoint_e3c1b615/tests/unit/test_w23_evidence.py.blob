"""Portable tests for the W23 immutable evidence and tooling successor."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from scripts.w23_architecture.evidence import (
    EvidenceError,
    artifact_ref,
    authenticate_artifact,
    verify_historical_binding,
    verify_witness,
)

ROOT = Path(__file__).resolve().parents[2]
WITNESS = "quality/w23/acceptance_tooling_witness_v001.json"
PREDECESSOR = "quality/w23/compatibility_closure_evidence.json"
PRODUCTION = "quality/w23/cycle_report_after_compatibility_retirement.json"


def _inputs(witness: dict | None = None) -> tuple[bytes, bytes, bytes, dict[str, bytes], dict[str, bytes]]:
    row = witness or json.loads((ROOT / WITNESS).read_bytes())
    raw = (json.dumps(row, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    tooling = {}
    for item in row["current_tooling"]:
        try:
            tooling[item["path"]] = (ROOT / item["path"]).read_bytes()
        except OSError:
            tooling[item["path"]] = b"synthetic missing member"
    snapshots = {item["path"]: (ROOT / item["path"]).read_bytes()
                 for item in row["historical_tooling_snapshots"]}
    return raw, (ROOT / PREDECESSOR).read_bytes(), (ROOT / PRODUCTION).read_bytes(), tooling, snapshots


def _verify(witness: dict | None = None, *, predecessor: bytes | None = None,
            production: bytes | None = None, tooling: dict[str, bytes] | None = None,
            snapshots: dict[str, bytes] | None = None) -> dict:
    raw, pred, prod, files, old = _inputs(witness)
    return verify_witness(raw, predecessor or pred, production or prod,
                          files if tooling is None else tooling,
                          old if snapshots is None else snapshots)


def test_artifact_ref_authenticates_explicit_predecessor_bytes() -> None:
    data = (ROOT / PREDECESSOR).read_bytes()
    ref = {"path": PREDECESSOR, "sha256": hashlib.sha256(data).hexdigest()}
    assert artifact_ref(ref) == ref
    assert authenticate_artifact(ref, data) == data
    with pytest.raises(EvidenceError):
        authenticate_artifact(ref, data + b" ")


def test_valid_predecessor_successor_tooling_and_historical_binding() -> None:
    witness = _verify()
    assert witness["predecessor"]["path"] == PREDECESSOR
    assert witness["production_evidence"]["path"] == PRODUCTION
    binding = witness["historical_authority_bindings"][0]
    artifact = (ROOT / binding["artifact"]["path"]).read_bytes()
    assert verify_historical_binding(binding, artifact) == "HISTORICAL_ONLY"


def test_wrong_predecessor_production_digest_and_stale_predecessor_fail() -> None:
    witness = json.loads((ROOT / WITNESS).read_bytes())
    changed = copy.deepcopy(witness)
    changed["predecessor"]["sha256"] = "0" * 64
    with pytest.raises(EvidenceError):
        _verify(changed)
    changed = copy.deepcopy(witness)
    changed["production_evidence"]["sha256"] = "0" * 64
    with pytest.raises(EvidenceError):
        _verify(changed)
    _, _, prod, _, _ = _inputs(witness)
    with pytest.raises(EvidenceError):
        _verify(predecessor=prod)


@pytest.mark.parametrize("reference", [
    {"path": "../outside", "sha256": "a" * 64},
    {"path": "quality/evidence.json", "sha256": "A" * 64},
    {"path": "quality/evidence.json", "sha256": "a" * 64, "extra": True},
])
def test_malformed_artifact_references_fail(reference: dict) -> None:
    with pytest.raises(EvidenceError):
        artifact_ref(reference)


@pytest.mark.parametrize("field,value", [
    ("modules", 882), ("largest_cross_domain_family", 5),
    ("total_cross_domain_family_membership", 3),
    ("f1_separated", False), ("f2_modules", []),
])
def test_wrong_production_identity_or_f2_membership_fails(field: str, value: object) -> None:
    witness = json.loads((ROOT / WITNESS).read_bytes())
    witness["production_identity"][field] = value
    with pytest.raises(EvidenceError):
        _verify(witness)


def test_changed_missing_and_unexpected_tooling_fail() -> None:
    raw, pred, prod, files, snapshots = _inputs()
    path = next(iter(files))
    mutated = dict(files)
    mutated[path] += b" changed"
    with pytest.raises(EvidenceError):
        verify_witness(raw, pred, prod, mutated, snapshots)
    missing = dict(files)
    missing.pop(path)
    with pytest.raises(EvidenceError):
        verify_witness(raw, pred, prod, missing, snapshots)
    unexpected = dict(files)
    unexpected["scripts/unexpected.py"] = b"synthetic"
    with pytest.raises(EvidenceError):
        verify_witness(raw, pred, prod, unexpected, snapshots)


def test_missing_unexpected_duplicate_and_malformed_tooling_members_fail() -> None:
    witness = json.loads((ROOT / WITNESS).read_bytes())
    for change in ("missing", "unexpected", "duplicate", "malformed"):
        selected = copy.deepcopy(witness)
        rows = selected["current_tooling"]
        if change == "missing":
            rows.pop()
        elif change == "unexpected":
            rows.append({"path": "scripts/unexpected.py", "sha256": "a" * 64})
            rows.sort(key=lambda item: item["path"])
        elif change == "duplicate":
            rows.append(copy.deepcopy(rows[-1]))
            rows.sort(key=lambda item: item["path"])
        else:
            rows[0]["sha256"] = "G" * 64
        with pytest.raises(EvidenceError):
            _verify(selected)


def test_historical_binding_cannot_be_promoted_to_current_authority() -> None:
    witness = json.loads((ROOT / WITNESS).read_bytes())
    binding = copy.deepcopy(witness["historical_authority_bindings"][0])
    binding["current_authority"] = True
    with pytest.raises(EvidenceError):
        _verify({**witness, "historical_authority_bindings": [binding]})

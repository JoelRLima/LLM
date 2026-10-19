"""Adversarial tests of the inactive prospective path; fixtures are simulations."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from scripts import w23_authority as authority
from scripts.w23_authority_transaction import SELECTOR


def encoded(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True) + "\n").encode()


@pytest.fixture
def proposal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, dict[str, Any]]:
    source, bundle = tmp_path / "source", tmp_path / "bundle"
    source.mkdir()
    bundle.mkdir()
    (source / "scripts").mkdir()
    (source / "scripts/w23_authority.py").write_bytes(b"verifier fixture\n")
    data = b"verifier fixture\n"
    oid = authority.hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).digest()
    tree = authority._tree({"scripts": {"w23_authority.py": ("100644", oid)}}, "sha1").hex()
    manifest = {"files": [{"path": "scripts/w23_authority.py", "mode": "100644", "bytes": len(data),
                          "sha256": authority.digest(data)}], "source_tree": tree, "object_format": "sha1",
                "workspace_context": {"operational_roots": []}}
    predecessor = encoded({"schema_version": 1})
    monkeypatch.setattr(authority, "PREDECESSOR_SHA256", authority.digest(predecessor))
    monkeypatch.setattr(authority, "parse_manifest", lambda raw, expected: json.loads(raw))
    monkeypatch.setattr(authority, "verify_successor", lambda *args: {
        "historical_integrity": "SIMULATED", "current_identity": "SIMULATED"})
    manifest["lineage"] = {"authority_predecessor": {"path": "predecessor.json", "sha256": authority.digest(predecessor)},
        "inherited_observation": {"path": "SPEC.md", "sha256": authority.digest(b"spec\n")}, "acceptance_records": []}
    manifest["selected_evidence"] = []
    files = {"predecessor.json": predecessor, "manifest.json": encoded(manifest), "CONTRACT.md": b"contract\n",
             "SPEC.md": b"spec\n", "tooling.json": b"{}\n"}
    for name in authority.REQUIRED_GATES:
        files["gate-" + name + ".json"] = encoded({"schema": "W23_GATE_OBSERVATION", "gate": name,
            "source_tree": tree, "state": "UNKNOWN", "evidence_refs": []})
    for name, value in files.items():
        (bundle / name).write_bytes(value)
    def ref(name: str) -> dict[str, str]:
        return {"path": name, "sha256": authority.digest(files[name])}
    p: dict[str, Any] = {"schema": "W23_PROSPECTIVE_AUTHORITY", "version": 10, "state": "PROPOSED_INACTIVE",
        "grants": dict(authority.GRANTS), "predecessor": ref("predecessor.json"), "candidate": ref("manifest.json"),
        "contract": ref("CONTRACT.md"), "spec": ref("SPEC.md"), "tooling_record": ref("tooling.json"),
        "verifier": {"path": "scripts/w23_authority.py", "sha256": authority.digest(data)},
        "historical_index": {"pin": authority.HISTORICAL_INDEX_PIN, "state": "UNVERIFIED_HISTORICAL_DIVERGENCE",
            "policy": "prospective_identity_does_not_repair_historical_raw_pin"},
        "bindings": [{"source": name, "destination": "quality/w23/authority/v010-cand03/" + name,
                      "sha256": authority.digest(value)} for name, value in files.items()],
        "gates": {name: {"state": "UNKNOWN", "source_tree": tree, "evidence": ref("gate-" + name + ".json")}
                  for name in authority.REQUIRED_GATES}}
    return source, bundle, p


def verify(fixture: tuple[Path, Path, dict[str, Any]]) -> dict[str, Any]:
    source, bundle, p = fixture
    data = encoded(p)
    return authority.verify_prospective(source, bundle, data, authority.digest(data))


def test_inactive_prospect_does_not_grant_acceptance(proposal: tuple[Path, Path, dict[str, Any]]) -> None:
    result = verify(proposal)
    assert result["status"] == "PROSPECTIVE_VERIFIED_INACTIVE"
    assert result["canonical_acceptance"] == "NOT_GRANTED"
    assert result["mandatory_gates_passed"] is False
    assert result["grants"] == authority.GRANTS
    assert result["historical_integrity"] == "SIMULATED"
    assert result["historical_raw_index"] == "UNVERIFIED_HISTORICAL_DIVERGENCE"


@pytest.mark.parametrize("field", ["selection", "activation", "merge", "publication"])
@pytest.mark.parametrize("value", [True, 0])
def test_no_self_grant(proposal: tuple[Path, Path, dict[str, Any]], field: str, value: object) -> None:
    proposal[2]["grants"][field] = value
    with pytest.raises(authority.AuthorityError, match="denied grants"):
        verify(proposal)


@pytest.mark.parametrize("mutation", ["state", "index", "predecessor", "missing_gate", "gate_tree", "simulated_gate",
                                      "binding_escape", "duplicate_binding", "missing_binding", "verifier"])
def test_prospective_mutations_fail_closed(proposal: tuple[Path, Path, dict[str, Any]], mutation: str) -> None:
    p = proposal[2]
    if mutation == "state":
        p["state"] = "ACTIVE"
    elif mutation == "index":
        p["historical_index"]["state"] = "PASS"
    elif mutation == "predecessor":
        p["predecessor"]["sha256"] = "0" * 64
    elif mutation == "missing_gate":
        p["gates"].pop("full_pytest")
    elif mutation == "gate_tree":
        p["gates"]["full_pytest"]["source_tree"] = "0" * 40
    elif mutation == "simulated_gate":
        p["gates"]["full_pytest"]["state"] = "SIMULATED"
    elif mutation == "binding_escape":
        p["bindings"][0]["destination"] = SELECTOR
    elif mutation == "duplicate_binding":
        p["bindings"].append(copy.deepcopy(p["bindings"][0]))
    elif mutation == "missing_binding":
        p["bindings"].pop()
    else:
        p["verifier"]["path"] = "untrusted.py"
    with pytest.raises(authority.AuthorityError):
        verify(proposal)


@pytest.mark.parametrize("mutation", ["extra_source", "changed_source", "missing_contract", "changed_contract", "wrong_hash"])
def test_bytes_cannot_be_relabelled(proposal: tuple[Path, Path, dict[str, Any]], mutation: str) -> None:
    source, bundle, p = proposal
    if mutation == "extra_source":
        (source / "extra.py").write_bytes(b"extra\n")
    elif mutation == "changed_source":
        (source / "scripts/w23_authority.py").write_bytes(b"changed\n")
    elif mutation == "missing_contract":
        (bundle / "CONTRACT.md").unlink()
    elif mutation == "changed_contract":
        (bundle / "CONTRACT.md").write_bytes(b"changed\n")
    else:
        with pytest.raises(authority.AuthorityError):
            authority.verify_prospective(source, bundle, encoded(p), "0" * 64)
        return
    with pytest.raises((authority.AuthorityError, FileNotFoundError)):
        verify(proposal)


def test_candidate_mode_is_part_of_the_authenticated_git_tree(
    proposal: tuple[Path, Path, dict[str, Any]],
) -> None:
    _source, bundle, p = proposal
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"][0]["mode"] = "100755"
    raw = encoded(manifest)
    manifest_path.write_bytes(raw)
    digest = authority.digest(raw)
    p["candidate"]["sha256"] = digest
    for binding in p["bindings"]:
        if binding["source"] == "manifest.json":
            binding["sha256"] = digest

    with pytest.raises(authority.AuthorityError):
        verify(proposal)


def test_gate_evidence_from_another_tree_is_rejected(
    proposal: tuple[Path, Path, dict[str, Any]],
) -> None:
    _source, bundle, p = proposal
    row = p["gates"]["full_pytest"]["evidence"]
    path = row["path"]
    evidence_path = bundle / path
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    evidence["source_tree"] = "0" * 40
    raw = encoded(evidence)
    evidence_path.write_bytes(raw)
    digest = authority.digest(raw)
    row["sha256"] = digest
    for binding in p["bindings"]:
        if binding["source"] == path:
            binding["sha256"] = digest

    with pytest.raises(authority.AuthorityError, match="gate observation"):
        verify(proposal)


def test_prospective_verification_requires_external_digest_anchor(
    proposal: tuple[Path, Path, dict[str, Any]],
) -> None:
    source, bundle, p = proposal
    with pytest.raises(authority.AuthorityError, match="independently expected digest"):
        authority.verify_prospective(source, bundle, encoded(p), "")


def make_install_fixture(tmp_path: Path, bundle: Path) -> Path:
    from scripts.w23_authority_transaction import MARKER

    root = tmp_path / "installation-fixture"
    root.mkdir(mode=0o700)
    (root / Path(SELECTOR).parent).mkdir()
    (root / ".w23-disposable-authority").write_bytes(MARKER)
    (root / SELECTOR).write_bytes((bundle / "predecessor.json").read_bytes())
    return root


def test_transaction_rehearsal_is_inactive_and_selector_last(proposal: tuple[Path, Path, dict[str, Any]], tmp_path: Path) -> None:
    from scripts.w23_authority_transaction import rehearse

    _, bundle, p = proposal
    root = make_install_fixture(tmp_path, bundle)
    raw = encoded(p)
    result = rehearse(root, bundle, raw, authority.digest(raw))
    assert result["state"] == "SIMULATED"
    assert result["selector_operation"] == "LAST"
    assert result["activation_authorized"] is False
    assert (root / SELECTOR).read_bytes() == raw


@pytest.mark.parametrize("failure_index", range(5))
def test_intermediate_failure_rolls_back_owned_files_only(
    proposal: tuple[Path, Path, dict[str, Any]], tmp_path: Path, failure_index: int,
) -> None:
    from scripts.w23_authority_transaction import rehearse

    _, bundle, p = proposal
    root = make_install_fixture(tmp_path, bundle)
    old = (root / SELECTOR).read_bytes()
    unrelated = root / "unrelated.txt"
    unrelated.write_bytes(b"preserve\n")
    def fail(path: Path, index: int) -> None:
        if index == failure_index:
            raise RuntimeError("injected write failure")
    with pytest.raises(RuntimeError, match="injected"):
        rehearse(root, bundle, encoded(p), authority.digest(encoded(p)), after_write=fail)
    assert (root / SELECTOR).read_bytes() == old
    assert unrelated.read_bytes() == b"preserve\n"
    assert all(not (root / row["destination"]).exists() for row in p["bindings"])


def test_tampered_installed_artifact_is_retained_and_rollback_unconfirmed(
    proposal: tuple[Path, Path, dict[str, Any]], tmp_path: Path,
) -> None:
    from scripts.w23_authority_transaction import rehearse

    _, bundle, p = proposal
    root = make_install_fixture(tmp_path, bundle)
    def tamper(path: Path, index: int) -> None:
        if index == 0:
            path.write_bytes(b"foreign bytes\n")
    with pytest.raises(authority.AuthorityError, match="ROLLBACK_UNCONFIRMED"):
        rehearse(root, bundle, encoded(p), authority.digest(encoded(p)), after_write=tamper)
    assert (root / p["bindings"][0]["destination"]).read_bytes() == b"foreign bytes\n"
    assert (root / SELECTOR).read_bytes() == (bundle / "predecessor.json").read_bytes()


@pytest.mark.parametrize("mutation", ["missing_source", "existing_target", "git_worktree", "changed_selector"])
def test_transaction_preconditions_fail_closed(
    proposal: tuple[Path, Path, dict[str, Any]], tmp_path: Path, mutation: str,
) -> None:
    from scripts.w23_authority_transaction import rehearse

    _, bundle, p = proposal
    root = make_install_fixture(tmp_path, bundle)
    callback = None
    if mutation == "missing_source":
        (bundle / p["bindings"][0]["source"]).unlink()
    elif mutation == "existing_target":
        target = root / p["bindings"][0]["destination"]
        target.parent.mkdir(parents=True)
        target.write_bytes(b"preexisting\n")
    elif mutation == "git_worktree":
        (root / ".git").mkdir()
    else:
        def callback(path: Path, index: int) -> None:
            (root / SELECTOR).write_bytes(b"concurrent selector\n")
    with pytest.raises((authority.AuthorityError, FileNotFoundError)):
        rehearse(root, bundle, encoded(p), authority.digest(encoded(p)), after_write=callback)
    if mutation == "changed_selector":
        assert (root / SELECTOR).read_bytes() == b"concurrent selector\n"
    elif mutation == "existing_target":
        assert target.read_bytes() == b"preexisting\n"


@pytest.mark.parametrize("mutation", ["missing_gate", "wrong_approval_digest", "different_tree", "activation", "publication", "merge"])
def test_future_owner_approval_cannot_waive_gates_or_expand_scope(
    proposal: tuple[Path, Path, dict[str, Any]], mutation: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, bundle, p = proposal
    # Explicit simulation of completed gates, not evidence of a real approval.
    monkeypatch.setattr(authority, "verify_prospective", lambda *args: {
        "source_tree": "1" * 40, "mandatory_gates_passed": mutation != "missing_gate"})
    raw = encoded(p)
    record = {"schema": "W23_OWNER_SELECTION_APPROVAL", "proposal_sha256": authority.digest(raw),
              "source_tree": "1" * 40, "predecessor_sha256": authority.PREDECESSOR_SHA256,
              "decision": "APPROVE_CANONICAL_SELECTION", "activation_authorized": False,
              "publication_authorized": False, "merge_authorized": False}
    if mutation == "different_tree":
        record["source_tree"] = "0" * 40
    elif mutation in {"activation", "publication", "merge"}:
        record[mutation + "_authorized"] = True
    approval = encoded(record)
    expected = "0" * 64 if mutation == "wrong_approval_digest" else authority.digest(approval)
    with pytest.raises(authority.AuthorityError):
        authority.verify_owner_approval(source, bundle, raw, authority.digest(raw), approval, expected)


def test_future_approval_verification_does_not_install_or_activate(
    proposal: tuple[Path, Path, dict[str, Any]], monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, bundle, p = proposal
    monkeypatch.setattr(authority, "verify_prospective", lambda *args: {
        "source_tree": "1" * 40, "mandatory_gates_passed": True, "grants": dict(authority.GRANTS)})
    raw = encoded(p)
    approval = encoded({"schema": "W23_OWNER_SELECTION_APPROVAL", "proposal_sha256": authority.digest(raw),
        "source_tree": "1" * 40, "predecessor_sha256": authority.PREDECESSOR_SHA256,
        "decision": "APPROVE_CANONICAL_SELECTION", "activation_authorized": False,
        "publication_authorized": False, "merge_authorized": False})
    result = authority.verify_owner_approval(source, bundle, raw, authority.digest(raw), approval, authority.digest(approval))
    assert result["canonical_acceptance"] == "OWNER_APPROVED_BYTES_AND_GATES_VERIFIED"
    assert result["activation_authorized"] is False and result["green"] is False
    assert not (source / SELECTOR).exists()


def test_bound_runner_does_not_export_bytecode_outside_pip_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from scripts.w23_bounded_windows import _environment

    monkeypatch.setenv("PYTHONPYCACHEPREFIX", str(tmp_path / "inherited-prefix"))
    environment = _environment(tmp_path / "scratch")
    assert "PYTHONPYCACHEPREFIX" not in environment
    assert environment["PYTHONDONTWRITEBYTECODE"] == "1"
    assert Path(environment["TEMP"]).is_relative_to(tmp_path / "scratch")


def test_manifest_cannot_claim_a_different_historical_predecessor(proposal: tuple[Path, Path, dict[str, Any]]) -> None:
    _, bundle, p = proposal
    manifest = json.loads((bundle / "manifest.json").read_bytes())
    manifest["lineage"]["authority_predecessor"]["sha256"] = "0" * 64
    raw = encoded(manifest)
    (bundle / "manifest.json").write_bytes(raw)
    p["candidate"]["sha256"] = authority.digest(raw)
    for row in p["bindings"]:
        if row["source"] == "manifest.json":
            row["sha256"] = authority.digest(raw)
    with pytest.raises(authority.AuthorityError, match="lineage"):
        verify(proposal)

"""Permanent model-free W18-A01..A60 campaign gate."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from distribution.release_identity import UV_ASSET_URL, UV_SHA256, UV_VERSION
from scripts.run_wave18_adversarial import (
    PERMANENT_SCENARIOS,
    ScenarioFailure,
    _current_canonical_source_tree,
    run_campaign,
)
from scripts.uv_build_evidence import UV_EVIDENCE_SCHEMA, UV_REQUIRED_CASES

CRITICAL_IDENTITIES = {
    "W18-A05": "payload file hash/size mismatch after extraction",
    "W18-A06": "payload ZIP traversal/absolute member",
    "W18-A27": "unrelated llm-agent command collision",
    "W18-A28": "transient dev venv collision only",
    "W18-A29": "Machine PATH remains unchanged",
    "W18-A30": "mutex contention",
    "W18-A31": "abandoned mutex recovery",
    "W18-A32": "journal corrupt",
    "W18-A33": "journal PREPARED recovery",
    "W18-A34": "journal STAGED recovery",
    "W18-A35": "journal LAUNCHER_PROMOTED recovery",
    "W18-A36": "journal PATH_MUTATED recovery",
    "W18-A60": "no public release side effect",
}


def test_lightweight_campaign_reports_installed_scenarios_as_not_run() -> None:
    report = run_campaign()

    assert report["campaign"] == "W18-A01..W18-A60"
    assert report["total"] == 60
    assert report["status"] == "incomplete"
    assert report["not_run"] == 12
    assert report["failed"] == 0
    assert report["passed"] == 48
    assert [(item["id"], item["description"]) for item in report["results"]] == [
        (scenario.identifier, scenario.description) for scenario in PERMANENT_SCENARIOS
    ]


def test_permanent_semantic_identities_cannot_shift() -> None:
    identities = {scenario.identifier: scenario.description for scenario in PERMANENT_SCENARIOS}

    assert len(identities) == len(PERMANENT_SCENARIOS) == 60
    assert list(identities) == [f"W18-A{index:02d}" for index in range(1, 61)]
    assert {identifier: identities[identifier] for identifier in CRITICAL_IDENTITIES} == CRITICAL_IDENTITIES
    assert identities["W18-A37"] == "launcher promotion access denied"
    assert identities["W18-A45"] == "A to valid B"
    assert identities["W18-A50"] == "retention keeps previous known-good"
    assert identities["W18-A58"] == "W17 existing config handoff model-free"
    assert identities["W18-A59"] == "future alias/state namespace structural test"


def test_behavioral_scenarios_require_canonical_verifier_evidence(tmp_path: Path) -> None:
    scenarios = {
        "A45_valid_A_to_B": {"status": "passed"},
        "A46_invalid_pre_promotion": {"status": "passed"},
        "A47_post_promotion_rollback": {"status": "passed"},
        "A48_post_path_rollback": {"status": "passed"},
        "A49_active_stale_candidate": {"status": "passed"},
        "A49_inactive_stale_cleanup": {"status": "passed"},
        "A50_previous_known_good": {"status": "passed"},
        "A53_exact_unrelated_path_preservation": {"status": "passed"},
        "A54_missing_receipt": {"status": "passed"},
        "A55_corrupt_receipt": {"status": "passed"},
        "A57_first_run_installed_interactive": {"status": "passed"},
        "A58_existing_config_installed_interactive": {"status": "passed"},
    }
    evidence = tmp_path / "installed.json"
    candidate = "w18-" + "a" * 32
    scenarios["clean_offline_install"] = {"status": "passed", "candidate_id": candidate}
    evidence.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "status": "passed",
                "evidence_identity": {
                    "candidate_id": candidate,
                    "source_tree": _current_canonical_source_tree(),
                    "release_manifest_sha256": "c" * 64,
                },
                "valid_candidate_fixtures": {"A": candidate},
                "scenarios": scenarios,
            }
        ),
        encoding="utf-8",
    )
    uv_evidence = _uv_evidence_for(evidence, tmp_path / "uv-build-evidence.json")
    report = run_campaign(evidence, uv_build_evidence=uv_evidence)
    assert report["status"] == "passed"
    assert report["failed"] == 0
    assert report["not_run"] == 0
    assert report["installed_evidence"]["candidate_id"] == candidate
    assert len(report["installed_evidence"]["sha256"]) == 64
    assert report["installed_evidence"]["release_manifest_sha256"] == "c" * 64
    assert report["uv_build_evidence"]["sha256"]


def _uv_evidence_for(installed: Path, target: Path) -> Path:
    identity = json.loads(installed.read_text(encoding="utf-8"))["evidence_identity"]
    target.write_text(
        json.dumps(
            {
                "schema_version": UV_EVIDENCE_SCHEMA,
                "status": "passed",
                "candidate_id": identity["candidate_id"],
                "source_tree": identity["source_tree"],
                "release_manifest_sha256": identity["release_manifest_sha256"],
                "pinned_uv": {
                    "url": UV_ASSET_URL,
                    "archive_sha256": UV_SHA256,
                    "version": UV_VERSION,
                },
                "cases": {
                    name: {"status": "passed", "outcome": outcome}
                    for name, outcome in UV_REQUIRED_CASES.items()
                },
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return target


def _installed_evidence(tmp_path: Path, *, source_tree: str | None = None) -> Path:
    candidate = "w18-" + "a" * 32
    scenarios = {
        key: {"status": "passed"}
        for key in (
            "A45_valid_A_to_B",
            "A46_invalid_pre_promotion",
            "A47_post_promotion_rollback",
            "A48_post_path_rollback",
            "A49_active_stale_candidate",
            "A49_inactive_stale_cleanup",
            "A50_previous_known_good",
            "A53_exact_unrelated_path_preservation",
            "A54_missing_receipt",
            "A55_corrupt_receipt",
            "A57_first_run_installed_interactive",
            "A58_existing_config_installed_interactive",
        )
    }
    path = tmp_path / "installed.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "status": "passed",
                "evidence_identity": {
                    "candidate_id": candidate,
                    "source_tree": source_tree or _current_canonical_source_tree(),
                    "release_manifest_sha256": "d" * 64,
                },
                "valid_candidate_fixtures": {"A": candidate},
                "scenarios": {
                    **scenarios,
                    "clean_offline_install": {"status": "passed", "candidate_id": candidate},
                },
            }
        ),
        encoding="utf-8",
    )
    return path


def test_installed_evidence_from_different_valid_source_tree_is_rejected(tmp_path: Path) -> None:
    evidence = _installed_evidence(tmp_path, source_tree="b" * 40)

    with pytest.raises(ScenarioFailure, match="source identity"):
        run_campaign(evidence)


def test_installed_evidence_requires_canonical_release_manifest_hash(tmp_path: Path) -> None:
    evidence = _installed_evidence(tmp_path)
    document = json.loads(evidence.read_text(encoding="utf-8"))
    del document["evidence_identity"]["release_manifest_sha256"]
    evidence.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ScenarioFailure, match="source identity"):
        run_campaign(evidence)


def test_installed_evidence_rejects_non_hex_release_manifest_hash(tmp_path: Path) -> None:
    evidence = _installed_evidence(tmp_path)
    document = json.loads(evidence.read_text(encoding="utf-8"))
    document["evidence_identity"]["release_manifest_sha256"] = "g" * 64
    evidence.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ScenarioFailure, match="source identity"):
        run_campaign(evidence)


def test_modified_installed_evidence_changes_adversarial_binding_hash(tmp_path: Path) -> None:
    evidence = _installed_evidence(tmp_path)
    first = run_campaign(evidence)
    evidence.write_bytes(evidence.read_bytes() + b"\n")
    second = run_campaign(evidence)

    assert first["installed_evidence"]["sha256"] != second["installed_evidence"]["sha256"]


def test_a11_is_not_run_without_uv_build_evidence(tmp_path: Path) -> None:
    report = run_campaign(_installed_evidence(tmp_path))
    a11 = next(item for item in report["results"] if item["id"] == "W18-A11")

    assert a11["status"] == "not_run"
    assert report["status"] == "incomplete"


def test_a11_consumes_exact_uv_build_evidence_hash(tmp_path: Path) -> None:
    installed = _installed_evidence(tmp_path)
    uv_evidence = _uv_evidence_for(installed, tmp_path / "uv.json")

    report = run_campaign(installed, uv_build_evidence=uv_evidence)
    a11 = next(item for item in report["results"] if item["id"] == "W18-A11")

    assert a11["status"] == "passed"
    assert a11["evidence"] == "uv-build-evidence: " + ",".join(UV_REQUIRED_CASES)
    assert report["uv_build_evidence"]["sha256"] == hashlib.sha256(uv_evidence.read_bytes()).hexdigest()


@pytest.mark.parametrize("field", ["candidate_id", "source_tree", "release_manifest_sha256"])
def test_a11_rejects_stale_or_mismatched_identity(tmp_path: Path, field: str) -> None:
    installed = _installed_evidence(tmp_path)
    uv_evidence = _uv_evidence_for(installed, tmp_path / "uv.json")
    document = json.loads(uv_evidence.read_text(encoding="utf-8"))
    document[field] = {"candidate_id": "w18-" + "b" * 32, "source_tree": "b" * 40}.get(field, "b" * 64)
    uv_evidence.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ScenarioFailure, match="uv build evidence is invalid"):
        run_campaign(installed, uv_build_evidence=uv_evidence)


def test_a11_rejects_required_negative_case_not_passed(tmp_path: Path) -> None:
    installed = _installed_evidence(tmp_path)
    uv_evidence = _uv_evidence_for(installed, tmp_path / "uv.json")
    document = json.loads(uv_evidence.read_text(encoding="utf-8"))
    document["cases"]["authenticode"]["status"] = "failed"
    uv_evidence.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ScenarioFailure, match="authenticode"):
        run_campaign(installed, uv_build_evidence=uv_evidence)

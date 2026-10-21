"""Permanent model-free W18-A01..A60 campaign gate."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from test_candidate_manifest import _repo as _synthetic_candidate

from distribution.release_identity import UV_ASSET_URL, UV_SHA256, UV_VERSION
from scripts import run_wave18_adversarial as campaign
from scripts.run_wave18_adversarial import (
    PERMANENT_SCENARIOS,
    ScenarioFailure,
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


def test_campaign_cli_bootstraps_source_imports_in_isolated_python(tmp_path: Path) -> None:
    script = Path(campaign.__file__).resolve()
    child_env = {
        key: value
        for key, value in os.environ.items()
        if key.casefold() != "pythonpath"
    }
    result = subprocess.run(
        [sys.executable, "-I", "-B", str(script), "--help"],
        cwd=tmp_path,
        env=child_env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout.lower()


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
    selection = _selection_kwargs(tmp_path)
    selected_manifest = json.loads(selection["candidate_manifest"].read_bytes())
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
                    "source_tree": selected_manifest["source_tree"],
                    "release_manifest_sha256": "c" * 64,
                },
                "valid_candidate_fixtures": {"A": candidate},
                "scenarios": scenarios,
            }
        ),
        encoding="utf-8",
    )
    uv_evidence = _uv_evidence_for(evidence, tmp_path / "uv-build-evidence.json")
    report = run_campaign(evidence, uv_build_evidence=uv_evidence, **selection)
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
    selection = _selection_kwargs(tmp_path)
    selected_manifest = json.loads(selection["candidate_manifest"].read_bytes())
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
                    "source_tree": source_tree or selected_manifest["source_tree"],
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


def _selection_kwargs(tmp_path: Path) -> dict[str, object]:
    fixture = tmp_path / "synthetic"
    fixture.mkdir(exist_ok=True)
    manifest_path = fixture / "candidate.json"
    if manifest_path.exists():
        encoded = manifest_path.read_bytes()
        digest = hashlib.sha256(encoded).hexdigest()
    else:
        root, encoded, digest = _synthetic_candidate(fixture)
    return {
        "candidate_manifest": manifest_path,
        "candidate_manifest_sha256": digest,
        "candidate_root": root if not manifest_path.exists() else fixture / "repo",
    }


def test_invalid_explicit_campaign_selection_never_uses_w20_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    selection = _selection_kwargs(tmp_path)

    def forbidden_fallback() -> str:
        raise AssertionError("historical W20 fallback was reached")

    monkeypatch.setattr(campaign, "_current_canonical_source_tree", forbidden_fallback)
    with pytest.raises(ScenarioFailure, match="explicit candidate selection is invalid"):
        run_campaign(
            candidate_manifest=selection["candidate_manifest"],
            candidate_manifest_sha256="0" * 64,
            candidate_root=selection["candidate_root"],
        )


def test_no_selection_keeps_historical_campaign_provenance_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence = _installed_evidence(tmp_path)
    tree = json.loads(evidence.read_text(encoding="utf-8"))["evidence_identity"]["source_tree"]
    calls: list[bool] = []

    def historical_identity() -> str:
        calls.append(True)
        return tree

    monkeypatch.setattr(campaign, "_current_canonical_source_tree", historical_identity)
    report = run_campaign(evidence)
    assert calls == [True]
    assert report["installed_evidence"]["source_tree"] == tree


def test_installed_evidence_from_different_valid_source_tree_is_rejected(tmp_path: Path) -> None:
    evidence = _installed_evidence(tmp_path, source_tree="b" * 40)

    with pytest.raises(ScenarioFailure, match="source identity"):
        run_campaign(evidence, **_selection_kwargs(tmp_path))


def test_installed_evidence_requires_canonical_release_manifest_hash(tmp_path: Path) -> None:
    evidence = _installed_evidence(tmp_path)
    document = json.loads(evidence.read_text(encoding="utf-8"))
    del document["evidence_identity"]["release_manifest_sha256"]
    evidence.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ScenarioFailure, match="source identity"):
        run_campaign(evidence, **_selection_kwargs(tmp_path))


def test_installed_evidence_rejects_non_hex_release_manifest_hash(tmp_path: Path) -> None:
    evidence = _installed_evidence(tmp_path)
    document = json.loads(evidence.read_text(encoding="utf-8"))
    document["evidence_identity"]["release_manifest_sha256"] = "g" * 64
    evidence.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ScenarioFailure, match="source identity"):
        run_campaign(evidence, **_selection_kwargs(tmp_path))


def test_modified_installed_evidence_changes_adversarial_binding_hash(tmp_path: Path) -> None:
    evidence = _installed_evidence(tmp_path)
    selection = _selection_kwargs(tmp_path)
    first = run_campaign(evidence, **selection)
    evidence.write_bytes(evidence.read_bytes() + b"\n")
    second = run_campaign(evidence, **selection)

    assert first["installed_evidence"]["sha256"] != second["installed_evidence"]["sha256"]


def test_a11_is_not_run_without_uv_build_evidence(tmp_path: Path) -> None:
    report = run_campaign(_installed_evidence(tmp_path), **_selection_kwargs(tmp_path))
    a11 = next(item for item in report["results"] if item["id"] == "W18-A11")

    assert a11["status"] == "not_run"
    assert report["status"] == "incomplete"


def test_a11_consumes_exact_uv_build_evidence_hash(tmp_path: Path) -> None:
    installed = _installed_evidence(tmp_path)
    uv_evidence = _uv_evidence_for(installed, tmp_path / "uv.json")

    report = run_campaign(installed, uv_build_evidence=uv_evidence, **_selection_kwargs(tmp_path))
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
        run_campaign(installed, uv_build_evidence=uv_evidence, **_selection_kwargs(tmp_path))


def test_a11_rejects_required_negative_case_not_passed(tmp_path: Path) -> None:
    installed = _installed_evidence(tmp_path)
    uv_evidence = _uv_evidence_for(installed, tmp_path / "uv.json")
    document = json.loads(uv_evidence.read_text(encoding="utf-8"))
    document["cases"]["authenticode"]["status"] = "failed"
    uv_evidence.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ScenarioFailure, match="authenticode"):
        run_campaign(installed, uv_build_evidence=uv_evidence, **_selection_kwargs(tmp_path))

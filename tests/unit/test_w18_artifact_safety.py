from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.check_w18_artifact_safety import ArtifactSafetyError, check_artifact_safety
from scripts.verify_installed_product import _v3_bounded_report


def test_bounded_hash_marker_evidence_is_uploadable(tmp_path: Path) -> None:
    path = tmp_path / "safe.json"
    path.write_text(
        json.dumps(
            {
                "raw_transcript_sha256": "a" * 64,
                "transcript_bytes": 42,
                "normalized_transcript_markers": ["Digite /help"],
                "input_sequence_metadata": [{"sha256": "b" * 64}],
            }
        ),
        encoding="utf-8",
    )

    check_artifact_safety([path])


def test_installed_projection_drops_runner_paths_and_transcript_text() -> None:
    bounded = _v3_bounded_report(
        {
            "schema_version": 3,
            "status": "passed",
            "install_root": r"C:\Users\runneradmin\AppData\Local\install",
            "scenarios": {
                "fresh_shell": {"status": "passed", "path": r"C:\Users\runneradmin\PATH"},
            },
            "w17_installed_interactive": {
                "launcher_chain": {"stable_launcher": r"C:\Users\runneradmin\stable.cmd", "stable_launcher_sha256": "a" * 64},
                "cases": {"A57": {"application_home": r"C:\Users\runneradmin\home", "transcript_sha256": "b" * 64}},
            },
        }
    )

    assert "install_root" not in bounded
    assert "path" not in bounded["scenarios"]["fresh_shell"]
    assert "stable_launcher" not in bounded["w17_installed_interactive"]["launcher_chain"]
    assert "application_home" not in bounded["w17_installed_interactive"]["cases"]["A57"]


def test_real_shaped_clean_install_projection_drops_all_absolute_paths(tmp_path: Path) -> None:
    bounded = _v3_bounded_report(
        {
            "schema_version": 3,
            "status": "passed",
            "scenarios": {
                "clean_offline_install": {
                    "status": "passed",
                    "candidate_id": "w18-" + "a" * 32,
                    "candidate_python": r"C:\Users\Alice\AppData\Local\install\runtime\python.exe",
                    "stable_launcher": r"C:\Users\Alice\AppData\Local\install\bin\llm-agent.cmd",
                    "payload_inventory": r"C:\Users\Alice\AppData\Local\install\payload-files.json",
                }
            },
        }
    )

    clean_install = bounded["scenarios"]["clean_offline_install"]
    assert clean_install["candidate_id"] == "w18-" + "a" * 32
    assert clean_install["status"] == "passed"
    assert "candidate_python" not in clean_install
    assert "stable_launcher" not in clean_install
    assert "payload_inventory" not in clean_install

    path = tmp_path / "clean-offline-install-bounded.json"
    path.write_text(json.dumps(bounded), encoding="utf-8")
    check_artifact_safety([path])


def test_bounded_w17_projection_keeps_only_input_sequence_metadata(tmp_path: Path) -> None:
    bounded = _v3_bounded_report(
        {
            "w17_installed_interactive": {
                "cases": {
                    "A57": {
                        "inputs": ["secret scripted input"],
                        "input_sequence": ["secret scripted input"],
                        "input_sequence_metadata": [
                            {"ordinal": 1, "utf8_bytes": 21, "sha256": "a" * 64}
                        ],
                    }
                }
            }
        }
    )

    case = bounded["w17_installed_interactive"]["cases"]["A57"]
    assert "inputs" not in case
    assert "input_sequence" not in case
    assert case["input_sequence_metadata"][0]["sha256"] == "a" * 64
    path = tmp_path / "w17-input-sequence-bounded.json"
    path.write_text(json.dumps(bounded), encoding="utf-8")
    check_artifact_safety([path])


@pytest.mark.parametrize(
    "document",
    (
        {"transcript": "raw"},
        {"decoded_transcript": "raw"},
        {"api_key": "sk-test-secret-value-123456"},
        {"environment": {"PATH": "secret"}},
        {"path": "C:\\Users\\runneradmin\\AppData\\Local\\real-user"},
        {"diagnostics": "failure at C:\\Users\\runneradmin\\AppData\\Local\\runner-temp"},
        {"normalized_transcript_tail": "safe-looking but not allowlisted"},
        {"inputs": ["scripted value"]},
        {"input_sequence": ["scripted value"]},
    ),
)
def test_raw_or_secret_like_evidence_is_rejected(tmp_path: Path, document: dict[str, object]) -> None:
    path = tmp_path / "unsafe.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ArtifactSafetyError):
        check_artifact_safety([path])


@pytest.mark.parametrize(
    "value",
    (
        r"C:\Users\Alice\AppData\Local\file.json",
        r"C:\Users\Bob Smith\Documents\file.json",
        r"C:\Usuários\Érica Silva\arquivo.json",
        r"C:/Users/Alice/Documents/file.json",
        r"prefix C:\Users\Bob\file.json suffix",
        r"\\server\share\folder\file.json",
        "/home/user/.config/agent.json",
        "file:///C:/Users/Alice/file.json",
        "file:///home/alice/.config/agent.json",
    ),
)
def test_generic_local_path_detection_rejects_windows_unc_posix_and_file_uri(value: str, tmp_path: Path) -> None:
    path = tmp_path / "path.json"
    path.write_text(json.dumps({"diagnostics": value}), encoding="utf-8")

    with pytest.raises(ArtifactSafetyError):
        check_artifact_safety([path])


@pytest.mark.parametrize(
    "url",
    (
        "https://github.com/astral-sh/uv/releases/download/0.12.13/uv.zip",
        "http://example.test/a",
    ),
)
def test_http_urls_are_not_misclassified_as_drive_paths(url: str, tmp_path: Path) -> None:
    path = tmp_path / "url.json"
    path.write_text(json.dumps({"pinned_uv": {"url": url}, "uv": {"asset_url": url}}), encoding="utf-8")

    check_artifact_safety([path])


def test_secret_scanning_still_applies_inside_urls(tmp_path: Path) -> None:
    path = tmp_path / "url-secret.json"
    path.write_text(
        json.dumps({"pinned_uv": {"url": "https://example.test/download?token=sk-test-secret-value-123456"}}),
        encoding="utf-8",
    )

    with pytest.raises(ArtifactSafetyError):
        check_artifact_safety([path])


def test_safety_scanner_accepts_all_six_bounded_upload_shapes(tmp_path: Path) -> None:
    identity = {
        "candidate_id": "w18-" + "a" * 32,
        "source_tree": "b" * 40,
        "release_manifest_sha256": "c" * 64,
    }
    files = {
        "uv": {
            "schema_version": "W18-UV-BUILD-ADVERSARIAL-EVIDENCE-V2",
            "status": "passed",
            "pinned_uv": {"url": "https://github.com/astral-sh/uv/releases/download/0.12.13/uv.zip"},
            "uv": {"asset_url": "https://github.com/astral-sh/uv/releases/download/0.12.13/uv.zip"},
        },
        "installed": {
            "schema_version": 3,
            "status": "passed",
            "evidence_identity": identity,
            "scenarios": {"clean_offline_install": {"candidate_id": identity["candidate_id"], "status": "passed"}},
        },
        "w17": {
            "schema_version": "W18-W17-INSTALLED-INTERACTIVE-V1",
            "status": "passed",
            "evidence_identity": identity,
            "cases": {"A57": {"input_sequence_metadata": [{"ordinal": 1, "utf8_bytes": 1, "sha256": "d" * 64}]}},
        },
        "matrix": {
            "schema_version": "W18-CONPTY-LAYER-MATRIX-V1",
            "status": "diagnosed",
            "harness": "native-Windows-ConPTY",
            "layer_1": {
                "raw_transcript_sha256": "e" * 64,
                "normalized_transcript_sha256": "f" * 64,
                "normalized_transcript_markers": ["W18_CONPTY_SMOKE"],
                "output_bytes": 10,
                "diagnostics": {"read_calls": 1},
            },
        },
        "authority": {
            "schema_version": "W18-CONPTY-AUTHORITY-V2",
            "status": "passed_for_A57_A58",
            "source_evidence": {"installed_product_json_sha256": "1" * 64},
        },
        "adversarial": {
            "campaign": "W18-A01..A60",
            "status": "passed",
            "passed": 60,
            "failed": 0,
            "not_run": 0,
        },
    }
    paths = []
    for name, document in files.items():
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        paths.append(path)

    check_artifact_safety(paths)

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.conpty_authority import (
    ConPtyAuthorityError,
    build_conpty_authority_evidence,
    validate_conpty_authority_evidence,
)


def _documents(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    candidate = "w18-" + "a" * 32
    identity = {
        "candidate_id": candidate,
        "release_manifest_sha256": "b" * 64,
        "source_tree": "c" * 40,
    }
    interactive_document = {
        "schema_version": "W18-W17-INSTALLED-INTERACTIVE-V1",
        "status": "passed",
        "harness": "conpty",
        "evidence_identity": identity,
        "cases": {
            case_id: {
                "status": "passed",
                "child_exit_code": 0,
                "network_used": False,
                "provider_model_server_invocations": 0,
                "transcript_sha256": "d" * 64,
            }
            for case_id in ("A57", "A58")
        },
    }
    summary_document = {
        "schema_version": 3,
        "status": "passed",
        "evidence_identity": identity,
        "w17_installed_interactive": interactive_document,
    }
    matrix_document = {
        "schema_version": "W18-CONPTY-LAYER-MATRIX-V1",
        "status": "diagnosed",
        "candidate_id": candidate,
        "layer_1": {"status": "completed", "returncode": 0},
        "layer_2": {"status": "completed", "returncode": 0},
        "layer_3": [
            {"status": "completed", "returncode": 0},
            {"status": "completed", "returncode": 0},
        ],
        "layer_4": {"status": "completed", "returncode": 0},
        "layer_5": {"status": "error"},
    }
    summary = tmp_path / "installed-product-summary.json"
    interactive = tmp_path / "w17-installed-interactive.json"
    matrix = tmp_path / "conpty-layer-matrix.json"
    authority = tmp_path / "conpty-authority-evidence.json"
    summary.write_text(json.dumps(summary_document, sort_keys=True), encoding="utf-8")
    interactive.write_text(json.dumps(interactive_document, sort_keys=True), encoding="utf-8")
    matrix.write_text(json.dumps(matrix_document, sort_keys=True), encoding="utf-8")
    return summary, interactive, matrix, authority


def test_valid_64_hex_binding_passes(tmp_path: Path) -> None:
    summary, interactive, matrix, authority = _documents(tmp_path)
    build_conpty_authority_evidence(summary, interactive, matrix, authority)
    validated = validate_conpty_authority_evidence(authority, summary, interactive, matrix)
    assert validated["source_evidence"]["layer_matrix_json_sha256"]


def test_65_hex_binding_fails_closed(tmp_path: Path) -> None:
    summary, interactive, matrix, authority = _documents(tmp_path)
    build_conpty_authority_evidence(summary, interactive, matrix, authority)
    document = json.loads(authority.read_text(encoding="utf-8"))
    document["source_evidence"]["layer_matrix_json_sha256"] += "0"
    authority.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ConPtyAuthorityError, match="canonical lowercase 64-hex"):
        validate_conpty_authority_evidence(authority, summary, interactive, matrix)


def test_one_byte_modified_layer_matrix_fails_closed(tmp_path: Path) -> None:
    summary, interactive, matrix, authority = _documents(tmp_path)
    build_conpty_authority_evidence(summary, interactive, matrix, authority)
    matrix.write_bytes(matrix.read_bytes() + b" ")
    with pytest.raises(ConPtyAuthorityError, match="layer_matrix_json_sha256"):
        validate_conpty_authority_evidence(authority, summary, interactive, matrix)


def test_installed_interactive_binding_remains_exact(tmp_path: Path) -> None:
    summary, interactive, matrix, authority = _documents(tmp_path)
    build_conpty_authority_evidence(summary, interactive, matrix, authority)
    interactive.write_bytes(interactive.read_bytes() + b"\n")
    with pytest.raises(ConPtyAuthorityError, match="installed_interactive_json_sha256"):
        validate_conpty_authority_evidence(authority, summary, interactive, matrix)

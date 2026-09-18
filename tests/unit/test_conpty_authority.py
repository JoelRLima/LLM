from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

import pytest

from scripts.conpty_authority import (
    ConPtyAuthorityError,
    build_conpty_authority_evidence,
    validate_conpty_authority_evidence,
)
from scripts.conpty_layer_matrix import (
    FIRST_RUN_MARKER,
    LAYER_1_LABEL,
    LAYER_2_LABEL,
    LAYER_3_LABEL,
    LAYER_4_LABEL,
    LAYER_5_LABEL,
)
from scripts.w18_conpty import normalize_terminal_text


def _write_valid_raw_sidecar(matrix: Path) -> None:
    matrix_document = json.loads(matrix.read_text(encoding="utf-8"))
    layer_texts = {
        "layer_1": "W18_CONPTY_SMOKE\r\n",
        "layer_2": "W18_CONPTY_PYTHON\r\n",
        "layer_3_candidate_path": "llm-agent 0.2.0rc1\r\n",
        "layer_3_via_cmd": "llm-agent 0.2.0rc1\r\n",
        "layer_4": "llm-agent 0.2.0rc1\r\n",
        "layer_5": f"{FIRST_RUN_MARKER} neste perfil.\r\n",
    }
    matrix_layers = {
        "layer_1": matrix_document["layer_1"],
        "layer_2": matrix_document["layer_2"],
        "layer_3_candidate_path": matrix_document["layer_3"][0],
        "layer_3_via_cmd": matrix_document["layer_3"][1],
        "layer_4": matrix_document["layer_4"],
        "layer_5": matrix_document["layer_5"],
    }
    captures: dict[str, dict[str, str]] = {}
    for name, text in layer_texts.items():
        raw = text.encode("utf-8")
        normalized = normalize_terminal_text(text)
        raw_sha256 = hashlib.sha256(raw).hexdigest()
        matrix_layers[name]["raw_transcript_sha256"] = raw_sha256
        matrix_layers[name]["normalized_transcript_sha256"] = hashlib.sha256(
            normalized.encode("utf-8")
        ).hexdigest()
        captures[name] = {
            "raw_transcript_b64": base64.b64encode(raw).decode("ascii"),
            "raw_transcript_sha256": raw_sha256,
            "decoded_transcript": text,
        }
    matrix.write_text(json.dumps(matrix_document, sort_keys=True), encoding="utf-8")
    matrix.with_suffix(".raw.json").write_text(
        json.dumps(
            {
                "schema_version": "W18-CONPTY-RAW-CAPTURE-V1",
                "candidate_id": matrix_document["candidate_id"],
                "evidence_identity": matrix_document["evidence_identity"],
                "layers": captures,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
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
    layer_evidence = {
        "raw_transcript_sha256": "d" * 64,
        "normalized_transcript_sha256": "e" * 64,
    }
    matrix_document = {
        "schema_version": "W18-CONPTY-LAYER-MATRIX-V1",
        "status": "diagnosed",
        "harness": "native-Windows-ConPTY",
        "candidate_id": candidate,
        "evidence_identity": identity,
        "layer_1": {
            **layer_evidence,
            "label": LAYER_1_LABEL,
            "normalized_transcript_markers": ["W18_CONPTY_SMOKE"],
            "status": "completed",
            "returncode": 0,
        },
        "layer_2": {
            **layer_evidence,
            "label": LAYER_2_LABEL,
            "normalized_transcript_markers": ["W18_CONPTY_PYTHON"],
            "status": "completed",
            "returncode": 0,
        },
        "layer_3": [
            {
                **layer_evidence,
                "label": LAYER_3_LABEL,
                "invocation": "candidate_path",
                "normalized_transcript_markers": ["llm-agent "],
                "status": "completed",
                "returncode": 0,
            },
            {
                **layer_evidence,
                "label": LAYER_3_LABEL,
                "invocation": "via_cmd",
                "normalized_transcript_markers": ["llm-agent "],
                "status": "completed",
                "returncode": 0,
            },
        ],
        "layer_4": {
            **layer_evidence,
            "label": LAYER_4_LABEL,
            "normalized_transcript_markers": ["llm-agent "],
            "status": "completed",
            "returncode": 0,
        },
        "layer_5": {
            **layer_evidence,
            "label": LAYER_5_LABEL,
            "status": "error",
            "outcome": "bounded_prompt_observed_and_terminated",
            "termination": "after_marker",
            "marker": FIRST_RUN_MARKER,
            "normalized_transcript_markers": [FIRST_RUN_MARKER],
        },
    }
    summary = tmp_path / "installed-product-summary.json"
    interactive = tmp_path / "w17-installed-interactive.json"
    matrix = tmp_path / "conpty-layer-matrix.json"
    authority = tmp_path / "conpty-authority-evidence.json"
    summary.write_text(json.dumps(summary_document, sort_keys=True), encoding="utf-8")
    interactive.write_text(json.dumps(interactive_document, sort_keys=True), encoding="utf-8")
    matrix.write_text(json.dumps(matrix_document, sort_keys=True), encoding="utf-8")
    _write_valid_raw_sidecar(matrix)
    return summary, interactive, matrix, authority


def test_valid_64_hex_binding_passes(tmp_path: Path) -> None:
    summary, interactive, matrix, authority = _documents(tmp_path)
    build_conpty_authority_evidence(summary, interactive, matrix, authority)
    validated = validate_conpty_authority_evidence(authority, summary, interactive, matrix)
    assert validated["source_evidence"]["layer_matrix_json_sha256"]


def test_authority_creation_requires_runner_local_raw_capture(tmp_path: Path) -> None:
    summary, interactive, matrix, authority = _documents(tmp_path)
    matrix.with_suffix(".raw.json").unlink()

    with pytest.raises(ConPtyAuthorityError, match="required during authority creation"):
        build_conpty_authority_evidence(summary, interactive, matrix, authority)


def test_posthoc_authority_validation_does_not_require_discarded_raw_capture(tmp_path: Path) -> None:
    summary, interactive, matrix, authority = _documents(tmp_path)
    build_conpty_authority_evidence(summary, interactive, matrix, authority)
    matrix.with_suffix(".raw.json").unlink()

    validated = validate_conpty_authority_evidence(authority, summary, interactive, matrix)

    assert validated["status"] == "passed_for_A57_A58"


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


def test_layer_5_error_without_first_run_prompt_fails_closed(tmp_path: Path) -> None:
    summary, interactive, matrix, authority = _documents(tmp_path)
    build_conpty_authority_evidence(summary, interactive, matrix, authority)
    document = json.loads(matrix.read_text(encoding="utf-8"))
    document["layer_5"]["normalized_transcript_markers"] = []
    matrix.write_text(json.dumps(document, sort_keys=True), encoding="utf-8")

    with pytest.raises(ConPtyAuthorityError, match="normalized marker"):
        validate_conpty_authority_evidence(authority, summary, interactive, matrix)


def test_wrong_layer_label_fails_closed(tmp_path: Path) -> None:
    summary, interactive, matrix, authority = _documents(tmp_path)
    build_conpty_authority_evidence(summary, interactive, matrix, authority)
    document = json.loads(matrix.read_text(encoding="utf-8"))
    document["layer_2"]["label"] = "wrong-layer"
    matrix.write_text(json.dumps(document, sort_keys=True), encoding="utf-8")

    with pytest.raises(ConPtyAuthorityError, match="layer_2"):
        validate_conpty_authority_evidence(authority, summary, interactive, matrix)


def test_runner_local_raw_capture_is_hash_bound_but_not_public_matrix(tmp_path: Path) -> None:
    summary, interactive, matrix, authority = _documents(tmp_path)
    matrix_document = json.loads(matrix.read_text(encoding="utf-8"))
    layer_texts = {
        "layer_1": "W18_CONPTY_SMOKE\r\n",
        "layer_2": "W18_CONPTY_PYTHON\r\n",
        "layer_3_candidate_path": "llm-agent 0.2.0rc1\r\n",
        "layer_3_via_cmd": "llm-agent 0.2.0rc1\r\n",
        "layer_4": "llm-agent 0.2.0rc1\r\n",
        "layer_5": f"{FIRST_RUN_MARKER} neste perfil.\r\n",
    }
    matrix_layers = {
        "layer_1": matrix_document["layer_1"],
        "layer_2": matrix_document["layer_2"],
        "layer_3_candidate_path": matrix_document["layer_3"][0],
        "layer_3_via_cmd": matrix_document["layer_3"][1],
        "layer_4": matrix_document["layer_4"],
        "layer_5": matrix_document["layer_5"],
    }
    captures: dict[str, dict[str, str]] = {}
    for name, text in layer_texts.items():
        raw = text.encode("utf-8")
        normalized = normalize_terminal_text(text)
        matrix_layers[name]["raw_transcript_sha256"] = hashlib.sha256(raw).hexdigest()
        matrix_layers[name]["normalized_transcript_sha256"] = hashlib.sha256(
            normalized.encode("utf-8")
        ).hexdigest()
        captures[name] = {
            "raw_transcript_b64": base64.b64encode(raw).decode("ascii"),
            "decoded_transcript": text,
        }
    matrix.write_text(json.dumps(matrix_document, sort_keys=True), encoding="utf-8")
    sidecar = matrix.with_suffix(".raw.json")
    sidecar.write_text(
        json.dumps(
            {
                "schema_version": "W18-CONPTY-RAW-CAPTURE-V1",
                "candidate_id": matrix_document["candidate_id"],
                "evidence_identity": matrix_document["evidence_identity"],
                "layers": captures,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    build_conpty_authority_evidence(summary, interactive, matrix, authority)
    assert "transcript" not in json.loads(matrix.read_text(encoding="utf-8"))["layer_5"]

    sidecar_document = json.loads(sidecar.read_text(encoding="utf-8"))
    sidecar_document["layers"]["layer_1"]["raw_transcript_b64"] = base64.b64encode(b"tampered").decode("ascii")
    sidecar_document["layers"]["layer_1"]["decoded_transcript"] = "tampered"
    sidecar.write_text(json.dumps(sidecar_document, sort_keys=True), encoding="utf-8")
    with pytest.raises(ConPtyAuthorityError, match="raw transcript SHA-256"):
        build_conpty_authority_evidence(summary, interactive, matrix, authority)

    sidecar_document["layers"]["layer_1"]["raw_transcript_b64"] = base64.b64encode(
        b"W18_CONPTY_SMOKE\r\n"
    ).decode("ascii")
    sidecar_document["layers"]["layer_1"]["decoded_transcript"] = "forged W18_CONPTY_SMOKE"
    sidecar.write_text(json.dumps(sidecar_document, sort_keys=True), encoding="utf-8")
    with pytest.raises(ConPtyAuthorityError, match="decoded transcript"):
        build_conpty_authority_evidence(summary, interactive, matrix, authority)

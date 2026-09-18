from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts import conpty_layer_matrix as producer
from scripts.w18_conpty import ConPtyResult


def test_layer_matrix_producer_records_all_bounded_layers(monkeypatch, tmp_path: Path) -> None:
    candidate_id = "w18-" + "a" * 32
    candidate = tmp_path / "candidate"
    runtime = candidate / "runtime" / "python.exe"
    candidate_launcher = candidate / "bin" / "llm-agent.cmd"
    stable = tmp_path / "install" / "bin" / "llm-agent.cmd"
    runtime.parent.mkdir(parents=True)
    candidate_launcher.parent.mkdir(parents=True)
    stable.parent.mkdir(parents=True)
    runtime.write_bytes(b"runtime")
    candidate_launcher.write_text("@echo off\r\n", encoding="utf-8")
    stable.write_text("@echo off\r\n", encoding="utf-8")

    def fake_run(
        command: tuple[str, ...],
        *,
        cwd: Path,
        environment: dict[str, str],
        interactions: tuple[()],
        timeout_seconds: float,
        terminate_after_marker: str | None = None,
    ) -> ConPtyResult:
        del cwd, environment, interactions, timeout_seconds
        if terminate_after_marker is not None:
            transcript = "Parece ser o primeiro uso neste perfil.\r\n"
            returncode = -1
        elif "W18_CONPTY_SMOKE" in " ".join(command):
            transcript = "W18_CONPTY_SMOKE\r\n"
            returncode = 0
        elif "W18_CONPTY_PYTHON" in " ".join(command):
            transcript = "W18_CONPTY_PYTHON\r\n"
            returncode = 0
        else:
            transcript = "llm-agent 0.2.0rc1\r\n"
            returncode = 0
        return ConPtyResult(
            returncode,
            transcript,
            (),
            {"fake": True},
            "d" * 64,
            len(transcript.encode("utf-8")),
        )

    monkeypatch.setattr(producer, "run_conpty", fake_run)
    matrix = producer.build_conpty_layer_matrix(
        candidate_id=candidate_id,
        candidate_root=candidate,
        stable_launcher=stable,
        cwd=tmp_path,
        environment={"PATH": "C:\\Windows\\System32"},
        application_home=tmp_path / "probe-home",
        first_run_home=tmp_path / "first-run-home",
        evidence_identity={
            "candidate_id": candidate_id,
            "source_tree": "b" * 40,
            "release_manifest_sha256": "c" * 64,
        },
    )

    assert matrix["schema_version"] == "W18-CONPTY-LAYER-MATRIX-V1"
    assert matrix["status"] == "diagnosed"
    assert matrix["layer_1"]["status"] == "completed"
    assert matrix["layer_2"]["status"] == "completed"
    assert {item["invocation"] for item in matrix["layer_3"]} == {"candidate_path", "via_cmd"}
    assert matrix["layer_4"]["returncode"] == 0
    assert matrix["layer_5"]["status"] == "error"
    assert matrix["layer_5"]["outcome"] == "bounded_prompt_observed_and_terminated"
    assert matrix["layer_5"]["normalized_transcript_markers"] == ["Parece ser o primeiro uso"]
    assert "transcript" not in matrix["layer_5"]
    assert "transcript" not in matrix["layer_1"]

    output = tmp_path / "matrix.json"
    producer.write_conpty_layer_matrix(matrix, output)
    assert json.loads(output.read_text(encoding="utf-8"))["candidate_id"] == candidate_id


def test_matrix_failure_reports_bounded_hash_evidence_without_visible_tail() -> None:
    sentinel = "RAW_SECRET_SENTINEL_DO_NOT_LOG"
    raw = sentinel.encode("utf-8")
    result = ConPtyResult(
        0,
        sentinel,
        (),
        {"read_calls": 1},
        hashlib.sha256(raw).hexdigest(),
        len(raw),
        raw,
    )

    with pytest.raises(producer.ConPtyLayerMatrixError) as raised:
        producer._command_result("layer-test", result, marker="EXPECTED_MARKER")

    message = str(raised.value)
    assert "EXPECTED_MARKER" in message
    assert sentinel not in message
    assert result.raw_transcript_sha256 in message
    assert str(len(raw)) in message

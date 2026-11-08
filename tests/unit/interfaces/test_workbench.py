from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from llm_agent.application.workbench.artifacts import REPORT_PREVIEW_BYTES, WorkbenchArtifacts
from llm_agent.application.workbench.contracts import parse_visual_contribution
from llm_agent.outputs.models import OutputContentPolicy, OutputKind, OutputPublishRequest, OutputSource
from llm_agent.workspace.paths import AppPaths


@pytest.fixture
def artifacts(tmp_path):
    return WorkbenchArtifacts(AppPaths.discover(tmp_path / "home", env={}).for_workspace("example"))


def test_report_is_lazy_and_keeps_origin_and_exact_bytes(artifacts, tmp_path: Path):
    path = tmp_path / "report.json"
    observation = {"report_path": str(path), "run_id": "run-a", "report_completeness": "final", "receipt": {"status": "failed"}}
    reference = artifacts.offer_report(observation)
    assert reference.completeness == "final"
    assert reference.source == "TaskReportBuilder"
    assert reference.workspace_id == "example"
    assert reference.run_id == "run-a"
    with pytest.raises(FileNotFoundError):
        artifacts.read(reference)
    raw = '{"status": "failed", "errors": ["real failure"]}'
    path.write_text(raw, encoding="utf-8")
    content = artifacts.read(reference)
    assert content.text == raw
    assert content.sha256 == hashlib.sha256(raw.encode()).hexdigest()


def test_no_report_is_inferred_from_answer_or_success(artifacts, tmp_path):
    assert artifacts.offer_report({"answer": "report.json", "success": True}) is None
    reference = artifacts.offer_report({"report_path": str(tmp_path / "missing.json"), "status": "succeeded"})
    assert reference.completeness == "unknown"
    assert reference.run_id is None


def test_unoffered_and_foreign_workspace_reference_rejected(artifacts, tmp_path):
    path = tmp_path / "report.json"
    path.write_text("{}", encoding="utf-8")
    reference = artifacts.offer_report({"report_path": str(path)})
    with pytest.raises(ValueError, match="not offered"):
        artifacts.read(replace(reference, workspace_id="other"))
    with pytest.raises(ValueError, match="not offered"):
        artifacts.read(replace(reference, locator=str(tmp_path / "secret")))


def test_report_preview_is_bounded_and_never_hashes_partial_as_whole(artifacts, tmp_path):
    path = tmp_path / "report.json"
    path.write_bytes(b"x" * (REPORT_PREVIEW_BYTES + 100))
    reference = artifacts.offer_report({"report_path": str(path)})
    content = artifacts.read(reference)
    assert len(content.text) == REPORT_PREVIEW_BYTES
    assert content.truncated
    assert content.sha256 is None
    assert content.renderer == "text"


@pytest.mark.parametrize("kind", [OutputKind.DIFF, OutputKind.JSON, OutputKind.TEST, OutputKind.LOG, OutputKind.FILE])
def test_existing_outputs_are_read_without_republication(artifacts, kind):
    published = artifacts.outputs.publish(OutputPublishRequest(
        kind, OutputSource.WORKSPACE_QUERY, "Original", "a" * 40_000,
        OutputContentPolicy.PUBLIC_TEXT, force_artifact=True, run_id="run-source",
    ))
    reference, = artifacts.list_outputs()
    assert reference.artifact_id == published.artifact.output_id
    assert reference.run_id == "run-source"
    assert reference.completeness == "unknown"
    first = artifacts.read(reference)
    assert len(first.text) == 16_384
    second = artifacts.read(reference, offset=first.next_offset)
    assert second.text
    assert first.sha256 == published.artifact.payload_sha256
    assert len(artifacts.outputs.list()) == 1


def test_source_truncation_stays_partial(artifacts):
    artifacts.outputs.publish(OutputPublishRequest(
        OutputKind.LOG, OutputSource.WORKER_DIAGNOSTIC, "Parcial", "observado",
        OutputContentPolicy.PUBLIC_TEXT, source_truncated=True, force_artifact=True,
    ))
    reference, = artifacts.list_outputs()
    assert reference.completeness == "partial"
    assert artifacts.read(reference).truncated


@pytest.mark.parametrize("value", [
    {"kind": "log", "renderer": "webview"},
    {"kind": "log", "renderer": "text", "script": "evil.py"},
    {"kind": "log", "renderer": "text", "entrypoint": ["python", "evil.py"]},
    {"kind": "custom-plugin", "renderer": "text"},
    [],
])
def test_visual_contract_rejects_executable_and_unknown_fields(value):
    with pytest.raises(ValueError):
        parse_visual_contribution(value)


def test_visual_contract_is_declarative_json():
    contribution = parse_visual_contribution(json.loads('{"kind":"diff","renderer":"diff"}'))
    assert contribution.renderer == "diff"

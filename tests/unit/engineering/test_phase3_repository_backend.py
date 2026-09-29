from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

import llm_agent
from llm_agent.agent.engineering.backends.repository import (
    MAX_ENGINEERING_ACCEPTANCE_SUMMARY_BYTES,
    RepositoryBackend,
    _read_summary,
    _validate_and_project,
)
from llm_agent.agent.engineering.contracts import (
    EngineeringBackendProtocolError,
    EngineeringBackendStateIndeterminateError,
    EngineeringCaller,
    EngineeringExecutionContext,
    EngineeringRequest,
    SourceRepositoryContext,
)
from llm_agent.execution import CommandExecutionError, CommandRequest, CommandResult
from llm_agent.interfaces.cli.engineering import discover_source_repository_context
from llm_agent.workspace.paths import AppPaths

IDENTITY_A = "a" * 40 + ":" + "b" * 64 + ":" + "c" * 64
IDENTITY_B = "d" * 40 + ":" + "e" * 64 + ":" + "f" * 64


def valid_summary() -> dict[str, object]:
    return {
        "schema_version": 2,
        "evidence_level": "installed_deterministic",
        "mode": "clean-acceptance",
        "status": "passed",
        "acceptance": True,
        "candidate_identity": IDENTITY_A,
        "semantic_manifest_hash": "1" * 64,
        "wheel_sha256": "2" * 64,
        "task_files_in_wheel": False,
        "properties": [{"id": "engineering-import", "proof": "must-not-project"}],
        "detail": "must-not-project",
    }


def test_source_context_derives_from_running_package_not_cwd(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    context = discover_source_repository_context()
    expected_root = Path(llm_agent.__file__).resolve().parents[2]
    assert context is not None
    assert context.root != tmp_path
    assert context.root == expected_root
    assert (context.root / "pyproject.toml").is_file()
    assert (context.root / "src" / "llm_agent").is_dir()
    assert (context.root / "scripts" / "verify_installed_package.py").is_file()


def test_exact_safe_projector_strips_untrusted_detail() -> None:
    projected = _validate_and_project(valid_summary(), IDENTITY_A)
    assert projected == {
        "acceptance": True,
        "candidate_identity": IDENTITY_A,
        "evidence_level": "installed_deterministic",
        "mode": "clean-acceptance",
        "property_ids": ["engineering-import"],
        "schema_version": 2,
        "semantic_manifest_hash": "1" * 64,
        "status": "passed",
        "task_files_in_wheel": False,
        "wheel_sha256": "2" * 64,
    }
    assert "detail" not in projected and "proof" not in projected


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", 1),
        ("evidence_level", "source"),
        ("mode", "offline-diagnostic"),
        ("status", "unknown"),
        ("acceptance", False),
        ("candidate_identity", IDENTITY_B),
        ("semantic_manifest_hash", "x"),
        ("wheel_sha256", "x"),
        ("task_files_in_wheel", True),
        ("properties", [{"id": "Bad_ID"}]),
    ],
)
def test_each_invalid_semantic_fact_is_protocol_error(field: str, value: object) -> None:
    document = valid_summary()
    document[field] = value
    with pytest.raises(EngineeringBackendProtocolError):
        _validate_and_project(document, IDENTITY_A)


def test_duplicate_property_ids_are_rejected() -> None:
    document = valid_summary()
    document["properties"] = [{"id": "same"}, {"id": "same"}]
    with pytest.raises(EngineeringBackendProtocolError):
        _validate_and_project(document, IDENTITY_A)


def test_summary_read_is_bounded_before_parse(tmp_path: Path) -> None:
    path = tmp_path / "installed-acceptance.json"
    path.write_bytes(b"{" + b"x" * MAX_ENGINEERING_ACCEPTANCE_SUMMARY_BYTES)
    with pytest.raises(EngineeringBackendProtocolError):
        _read_summary(path)


def test_summary_duplicate_keys_rejected(tmp_path: Path) -> None:
    path = tmp_path / "installed-acceptance.json"
    path.write_text('{"status":"passed","status":"failed"}', encoding="utf-8")
    with pytest.raises(EngineeringBackendProtocolError):
        _read_summary(path)


def test_valid_failure_summary_projects_failed_truth() -> None:
    document = valid_summary()
    document["status"] = "failed"
    document["acceptance"] = False
    document["wheel_sha256"] = None
    projected = _validate_and_project(document, IDENTITY_A)
    assert projected["status"] == "failed" and projected["acceptance"] is False


class SetToken:
    def is_set(self) -> bool:
        return True


class FakeExecutor:
    def __init__(
        self,
        *,
        error: CommandExecutionError | None = None,
        result: CommandResult | None = None,
        summary: dict[str, object] | None = None,
    ) -> None:
        self.error = error
        self.result = result or CommandResult(0, b"", b"", 0.0)
        self.summary = summary

    def execute(self, request: CommandRequest) -> CommandResult:
        if self.summary is not None:
            argv = request.argv
            Path(argv[-1]).write_text(json.dumps(self.summary), encoding="utf-8")
        if self.error is not None:
            raise self.error
        return self.result


def backend_context(tmp_path: Path, *, cancelled: bool = False) -> EngineeringExecutionContext:
    return EngineeringExecutionContext(
        EngineeringCaller.AUTOMATION_HEADLESS,
        AppPaths.discover(tmp_path / "home"),
        None,
        SourceRepositoryContext(tmp_path, IDENTITY_A),
        frozenset(),
        lambda: datetime(2026, 1, 1, tzinfo=timezone.utc),
        lambda: 1.0,
        cancellation_token=SetToken() if cancelled else None,
    )


def test_cleanup_uncertainty_propagates_as_typed_indeterminate(tmp_path: Path) -> None:
    backend = RepositoryBackend(
        FakeExecutor(
            error=CommandExecutionError(
                "cleanup uncertain", code="CLEANUP_ERROR"
            )
        )
    )
    with pytest.raises(EngineeringBackendStateIndeterminateError):
        backend.execute(
            EngineeringRequest("acceptance.installed-package", {}), backend_context(tmp_path, cancelled=True)
        )


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object semantics are Windows-only")
def test_windows_job_association_failure_with_settled_cleanup_is_protocol_error(
    tmp_path: Path,
) -> None:
    backend = RepositoryBackend(
        FakeExecutor(
            error=CommandExecutionError(
                "association failed", code="WINDOWS_JOB_ASSOCIATION"
            )
        )
    )
    with pytest.raises(EngineeringBackendProtocolError):
        backend.execute(EngineeringRequest("acceptance.installed-package", {}), backend_context(tmp_path))


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object semantics are Windows-only")
def test_windows_job_close_uncertainty_overrides_apparent_success(
    tmp_path: Path,
) -> None:
    with pytest.raises(EngineeringBackendStateIndeterminateError):
        RepositoryBackend(
            FakeExecutor(
                error=CommandExecutionError(
                    "Job Object close did not confirm closure", code="CLEANUP_ERROR"
                ),
                summary=valid_summary(),
            )
        ).execute(
            EngineeringRequest("acceptance.installed-package", {}), backend_context(tmp_path)
        )

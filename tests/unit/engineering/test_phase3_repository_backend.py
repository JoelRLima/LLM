from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

import agent
from agent.engineering.backends import repository
from agent.engineering.backends.repository import (
    MAX_ENGINEERING_ACCEPTANCE_SUMMARY_BYTES,
    RepositoryBackend,
    _read_summary,
    _validate_and_project,
)
from agent.engineering.cli import discover_source_repository_context
from agent.engineering.contracts import (
    EngineeringBackendProtocolError,
    EngineeringBackendStateIndeterminateError,
    EngineeringCaller,
    EngineeringExecutionContext,
    EngineeringRequest,
    SourceRepositoryContext,
)
from agent.runtime.paths import AppPaths

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
    expected_root = Path(agent.__file__).resolve().parent.parent
    assert context is not None
    assert context.root != tmp_path
    assert context.root == expected_root
    assert (context.root / "pyproject.toml").is_file()
    assert (context.root / "agent").is_dir()
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


class FakeProcess:
    def __init__(self, returncode: int | None) -> None:
        self.returncode = returncode
        self.stdout = None
        self.stderr = None
        self.stdin = None

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        if self.returncode is None:
            self.returncode = -1
        return self.returncode


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


def patch_reader_lifecycle(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(repository, "start_readers", lambda *args: ([], object(), object(), None, []))
    monkeypatch.setattr(repository, "close_pipes", lambda process: None)
    monkeypatch.setattr(repository, "create_windows_job", lambda: object())
    monkeypatch.setattr(repository, "close_windows_job", lambda job: True)


def test_cleanup_uncertainty_propagates_as_typed_indeterminate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    patch_reader_lifecycle(monkeypatch)
    monkeypatch.setattr(repository, "assign_windows_job", lambda job, process: True)
    monkeypatch.setattr(repository, "terminate_process", lambda *args, **kwargs: "cleanup uncertain")
    backend = RepositoryBackend(lambda *args, **kwargs: FakeProcess(None))
    with pytest.raises(EngineeringBackendStateIndeterminateError):
        backend.execute(
            EngineeringRequest("acceptance.installed-package", {}), backend_context(tmp_path, cancelled=True)
        )


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object semantics are Windows-only")
def test_windows_job_association_failure_with_settled_cleanup_is_protocol_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    patch_reader_lifecycle(monkeypatch)
    monkeypatch.setattr(repository, "assign_windows_job", lambda job, process: False)
    monkeypatch.setattr(repository, "terminate_process", lambda *args, **kwargs: None)
    backend = RepositoryBackend(lambda *args, **kwargs: FakeProcess(None))
    with pytest.raises(EngineeringBackendProtocolError):
        backend.execute(EngineeringRequest("acceptance.installed-package", {}), backend_context(tmp_path))


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object semantics are Windows-only")
def test_windows_job_close_uncertainty_overrides_apparent_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    patch_reader_lifecycle(monkeypatch)
    monkeypatch.setattr(repository, "assign_windows_job", lambda job, process: True)
    monkeypatch.setattr(repository, "close_windows_job", lambda job: False)

    def popen(argv: tuple[str, ...], **kwargs: object) -> FakeProcess:
        del kwargs
        Path(argv[-1]).write_text(json.dumps(valid_summary()), encoding="utf-8")
        return FakeProcess(0)

    with pytest.raises(EngineeringBackendStateIndeterminateError):
        RepositoryBackend(popen).execute(
            EngineeringRequest("acceptance.installed-package", {}), backend_context(tmp_path)
        )

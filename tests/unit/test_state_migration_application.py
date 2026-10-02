from __future__ import annotations

from dataclasses import fields
from pathlib import Path
from typing import get_type_hints

import pytest

from llm_agent.agent.runtime.state_migration import StateMigrationError, StateMigrationReport
from llm_agent.agent.runtime.storage_contracts import StorageMigrationError
from llm_agent.application.context import AppPaths, WorkspaceContext
from llm_agent.application.state_migration import (
    StateMigrationFailedError,
    StateMigrationRequest,
    StateMigrationResult,
    migrate_state,
)


def _request(tmp_path: Path, *, workspace: Path | None = None) -> StateMigrationRequest:
    selected_workspace = workspace or tmp_path / "workspace"
    selected_workspace.mkdir(parents=True, exist_ok=True)
    return StateMigrationRequest(
        source=tmp_path / "legacy-runtime",
        workspace=selected_workspace,
        app_paths=AppPaths.discover(app_home=tmp_path / "app-home"),
    )


def test_application_state_migration_projects_finite_report_and_orders_workflow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import llm_agent.application.state_migration.operations as operations_module

    request = _request(tmp_path)
    events: list[str] = []
    observed: dict[str, object] = {}
    actual_workspace_context = WorkspaceContext

    class RecordingWorkspaceContext:
        @classmethod
        def create(cls, workspace: str | Path) -> WorkspaceContext:
            events.append("workspace")
            return actual_workspace_context.create(workspace)

    class Lease:
        @classmethod
        def begin_transient(cls, home: Path) -> Lease:
            events.append("lease")
            observed["home"] = home
            return cls()

        def close(self) -> None:
            events.append("close")

    class Bootstrap:
        def prepare(self, app_paths: AppPaths) -> None:
            events.append("bootstrap")
            observed["bootstrap_paths"] = app_paths

    def migrate(source: str | Path, destination: object) -> StateMigrationReport:
        events.append("migration")
        observed["source"] = source
        observed["destination"] = destination
        agent_resolved_source = str((tmp_path / "resolved-legacy-runtime").resolve())
        observed["report_source"] = agent_resolved_source
        return StateMigrationReport(
            source=agent_resolved_source,
            copied=("second.json", "first.json"),
            skipped=("equal.json",),
        )

    monkeypatch.setattr(operations_module, "WorkspaceContext", RecordingWorkspaceContext)
    monkeypatch.setattr(operations_module, "HomeLifecycleLease", Lease)
    monkeypatch.setattr(operations_module, "StorageBootstrap", Bootstrap)
    monkeypatch.setattr(operations_module, "migrate_legacy_state", migrate)

    result = migrate_state(request)

    workspace_id = actual_workspace_context.create(request.workspace).workspace_id
    assert observed["destination"] == request.app_paths.for_workspace(workspace_id)
    assert observed["bootstrap_paths"] is request.app_paths
    assert observed["home"] == request.app_paths.home_dir
    assert observed["source"] == request.source
    assert observed["report_source"] != str(request.source)
    assert events == ["workspace", "lease", "bootstrap", "migration", "close"]
    assert result == StateMigrationResult(
        source=str(observed["report_source"]),
        copied=("second.json", "first.json"),
        skipped=("equal.json",),
    )
    assert (result.copied_count, result.skipped_count) == (2, 1)
    assert tuple(field.name for field in fields(StateMigrationRequest)) == (
        "source", "workspace", "app_paths"
    )
    assert tuple(field.name for field in fields(StateMigrationResult)) == (
        "source", "copied", "skipped"
    )
    request_hints = get_type_hints(StateMigrationRequest)
    result_hints = get_type_hints(StateMigrationResult)
    operation_hints = get_type_hints(migrate_state)
    assert request_hints == {
        "source": str | Path,
        "workspace": str | Path,
        "app_paths": AppPaths,
    }
    assert result_hints == {
        "source": str,
        "copied": tuple[str, ...],
        "skipped": tuple[str, ...],
    }
    assert operation_hints == {
        "request": StateMigrationRequest,
        "return": StateMigrationResult,
    }


def test_workspace_failure_precedes_lease_and_begin_failure_has_no_second_close(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import llm_agent.application.state_migration.operations as operations_module

    request = StateMigrationRequest(
        source=tmp_path / "legacy-runtime",
        workspace=tmp_path / "missing-workspace",
        app_paths=AppPaths.discover(app_home=tmp_path / "app-home"),
    )
    lease_calls: list[str] = []

    class Lease:
        @classmethod
        def begin_transient(cls, _home: Path) -> Lease:
            lease_calls.append("begin")
            raise RuntimeError("begin failed")

    monkeypatch.setattr(operations_module, "HomeLifecycleLease", Lease)
    with pytest.raises(FileNotFoundError):
        migrate_state(request)
    assert lease_calls == []

    request = _request(tmp_path)
    with pytest.raises(RuntimeError, match="begin failed"):
        migrate_state(request)
    assert lease_calls == ["begin"]


def test_bootstrap_failure_skips_migration_and_closes_lease(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import llm_agent.application.state_migration.operations as operations_module

    request = _request(tmp_path)
    events: list[str] = []
    failure = StorageMigrationError("bootstrap failed")

    class Lease:
        @classmethod
        def begin_transient(cls, _home: Path) -> Lease:
            events.append("lease")
            return cls()

        def close(self) -> None:
            events.append("close")

    class Bootstrap:
        def prepare(self, _app_paths: AppPaths) -> None:
            events.append("bootstrap")
            raise failure

    def migration_must_not_run(*_args: object) -> StateMigrationReport:
        events.append("migration")
        raise AssertionError("migration must not run after bootstrap failure")

    monkeypatch.setattr(operations_module, "HomeLifecycleLease", Lease)
    monkeypatch.setattr(operations_module, "StorageBootstrap", Bootstrap)
    monkeypatch.setattr(operations_module, "migrate_legacy_state", migration_must_not_run)

    with pytest.raises(StorageMigrationError) as raised:
        migrate_state(request)

    assert raised.value is failure
    assert not isinstance(raised.value, StateMigrationFailedError)
    assert events == ["lease", "bootstrap", "close"]


def test_migration_error_is_translated_and_other_errors_are_not(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import llm_agent.application.state_migration.operations as operations_module

    request = _request(tmp_path)
    events: list[str] = []

    class Lease:
        @classmethod
        def begin_transient(cls, _home: Path) -> Lease:
            return cls()

        def close(self) -> None:
            events.append("close")

    class Bootstrap:
        def prepare(self, _app_paths: AppPaths) -> None:
            events.append("bootstrap")

    monkeypatch.setattr(operations_module, "HomeLifecycleLease", Lease)
    monkeypatch.setattr(operations_module, "StorageBootstrap", Bootstrap)
    agent_error = StateMigrationError("legacy migration failed")

    def fail_agent(*_args: object) -> StateMigrationReport:
        events.append("migration")
        raise agent_error

    monkeypatch.setattr(operations_module, "migrate_legacy_state", fail_agent)
    with pytest.raises(StateMigrationFailedError, match="legacy migration failed") as translated:
        migrate_state(request)
    assert translated.value.__cause__ is agent_error
    assert events == ["bootstrap", "migration", "close"]

    events.clear()

    def fail_unexpectedly(*_args: object) -> StateMigrationReport:
        events.append("migration")
        raise ValueError("unexpected")

    monkeypatch.setattr(operations_module, "migrate_legacy_state", fail_unexpectedly)
    with pytest.raises(ValueError, match="unexpected"):
        migrate_state(request)
    assert events == ["bootstrap", "migration", "close"]


def test_cli_migration_error_exit_and_bootstrap_failure_classification(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import llm_agent.application.state_migration.operations as operations_module
    from llm_agent.interfaces.cli.app import main

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    home = tmp_path / "app-home"
    missing_source = tmp_path / "missing-legacy"
    args = [
        "state", "migrate", "--from", str(missing_source),
        "--home", str(home), "--workspace", str(workspace),
    ]

    assert main(args) == 2
    assert "Runtime legado n\u00e3o encontrado" in capsys.readouterr().err

    class FailingBootstrap:
        def prepare(self, _app_paths: AppPaths) -> None:
            raise StorageMigrationError("bootstrap failed")

    monkeypatch.setattr(operations_module, "StorageBootstrap", FailingBootstrap)
    assert main(args) == 1
    assert "StorageMigrationError: bootstrap failed" in capsys.readouterr().err


def test_lease_close_failure_preserves_current_exception_masking(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import llm_agent.application.state_migration.operations as operations_module

    request = _request(tmp_path)

    class Lease:
        @classmethod
        def begin_transient(cls, _home: Path) -> Lease:
            return cls()

        def close(self) -> None:
            raise RuntimeError("lease close failed")

    class Bootstrap:
        def prepare(self, _app_paths: AppPaths) -> None:
            pass

    def fail_agent(*_args: object) -> StateMigrationReport:
        raise StateMigrationError("migration failed")

    monkeypatch.setattr(operations_module, "HomeLifecycleLease", Lease)
    monkeypatch.setattr(operations_module, "StorageBootstrap", Bootstrap)
    monkeypatch.setattr(operations_module, "migrate_legacy_state", fail_agent)

    with pytest.raises(RuntimeError, match="lease close failed"):
        migrate_state(request)

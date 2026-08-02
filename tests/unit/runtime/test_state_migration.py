import json
import sqlite3
from pathlib import Path

import pytest

from agent.runtime import state_migration
from agent.runtime.instance_lock import InstanceLock
from agent.runtime.paths import AppPaths
from agent.runtime.state_migration import StateMigrationError, migrate_legacy_state


def _workspace_paths(tmp_path: Path):
    return AppPaths.discover(tmp_path / "home", env={}).for_workspace("example-123")


def test_migration_copies_allowlisted_state_and_preserves_source(tmp_path: Path) -> None:
    source = tmp_path / "runtime"
    source.mkdir()
    memory = source / "agent_memory.json"
    memory.write_text(json.dumps({"notes": {"x": "y"}}), encoding="utf-8")
    metrics = source / "agent_metrics.jsonl"
    metrics.write_text('{"tokens": 1}\n', encoding="utf-8")
    database = source / "agent_memory.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE values_table (value TEXT)")
    destination = _workspace_paths(tmp_path)

    report = migrate_legacy_state(source, destination)

    assert destination.memory_file.read_bytes() == memory.read_bytes()
    assert destination.metrics_file.read_bytes() == metrics.read_bytes()
    assert destination.memory_db_file.exists()
    assert memory.exists()
    assert set(report.copied) == {
        "agent_memory.json",
        "agent_metrics.jsonl",
        "agent_memory.db",
    }


def test_migration_is_idempotent_for_equal_files(tmp_path: Path) -> None:
    source = tmp_path / "runtime"
    source.mkdir()
    (source / "agent_memory.json").write_text("{}", encoding="utf-8")
    destination = _workspace_paths(tmp_path)

    migrate_legacy_state(source, destination)
    report = migrate_legacy_state(source, destination)

    assert report.copied == ()
    assert report.skipped == ("agent_memory.json",)


def test_migration_fails_closed_on_conflict(tmp_path: Path) -> None:
    source = tmp_path / "runtime"
    source.mkdir()
    (source / "agent_memory.json").write_text('{"source": true}', encoding="utf-8")
    destination = _workspace_paths(tmp_path)
    destination.data_dir.mkdir(parents=True)
    destination.memory_file.write_text('{"destination": true}', encoding="utf-8")

    with pytest.raises(StateMigrationError, match="conflitante"):
        migrate_legacy_state(source, destination)

    assert json.loads(destination.memory_file.read_text(encoding="utf-8")) == {
        "destination": True
    }


def test_migration_rejects_malformed_json_before_copying_anything(tmp_path: Path) -> None:
    source = tmp_path / "runtime"
    source.mkdir()
    (source / "agent_memory.json").write_text("{", encoding="utf-8")
    (source / "chat_history.json").write_text("[]", encoding="utf-8")
    destination = _workspace_paths(tmp_path)

    with pytest.raises(StateMigrationError, match="JSON inválido"):
        migrate_legacy_state(source, destination)

    assert not destination.chat_history_file.exists()


def test_migration_refuses_workspace_with_live_application_lock(tmp_path: Path) -> None:
    source = tmp_path / "runtime"
    source.mkdir()
    (source / "agent_memory.json").write_text("{}", encoding="utf-8")
    destination = _workspace_paths(tmp_path)

    with InstanceLock.create(destination.lock_file):
        with pytest.raises(StateMigrationError, match="em uso"):
            migrate_legacy_state(source, destination)

    assert not destination.memory_file.exists()


def test_migration_rolls_back_files_when_promotion_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "runtime"
    source.mkdir()
    (source / "agent_memory.json").write_text("{}", encoding="utf-8")
    (source / "chat_history.json").write_text("[]", encoding="utf-8")
    destination = _workspace_paths(tmp_path)
    real_copy = state_migration._copy_atomic
    calls = 0

    def fail_second_copy(source_path: Path, destination_path: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("falha injetada")
        real_copy(source_path, destination_path)

    monkeypatch.setattr(state_migration, "_copy_atomic", fail_second_copy)

    with pytest.raises(StateMigrationError, match="revertidos"):
        migrate_legacy_state(source, destination)

    assert not destination.memory_file.exists()
    assert not destination.chat_history_file.exists()
    assert not destination.lock_file.exists()

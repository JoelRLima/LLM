import json
import sqlite3
from types import SimpleNamespace

import pytest

from agent.memory import json_persistence
from agent.memory.json_persistence import AtomicJsonWriteError
from agent.memory.memory import (
    AgentMemory,
    MemoryDatabaseError,
    MemoryLoadError,
)
from agent.runtime import paths
from agent.tool_executor import ToolExecutor


def test_memory_save_load_excludes_sqlite_sections(tmp_path, monkeypatch):
    temp_memory = tmp_path / "agent_memory.json"
    monkeypatch.setattr(paths, "MEMORY_FILE", str(temp_memory))

    mem = AgentMemory()
    mem.state["project_map"] = {"a.py": "ok"}
    mem.state["key_findings"] = {"x": "valor"}
    mem.state["file_summaries"] = {"a.py": "resumo"}
    mem.save_to_file()

    loaded = json.loads(temp_memory.read_text(encoding="utf-8"))
    assert "project_map" in loaded
    assert "key_findings" not in loaded
    assert "file_summaries" not in loaded


def test_memory_load_from_file_does_not_overwrite_sqlite_sections(tmp_path, monkeypatch):
    temp_memory = tmp_path / "agent_memory.json"
    monkeypatch.setattr(paths, "MEMORY_FILE", str(temp_memory))

    mem = AgentMemory()
    mem.state["project_map"] = {"a.py": "ok"}
    mem.save_to_file()

    temp_memory.write_text(json.dumps({
        "project_map": {"b.py": "novo"},
        "key_findings": {"y": "valor"},
        "file_summaries": {"b.py": "novo resumo"}
    }), encoding="utf-8")

    mem.clear()
    assert mem.state["project_map"] == {}
    mem.load_from_file()
    assert mem.state["project_map"]["b.py"] == "novo"
    assert mem.state["key_findings"] == {}
    assert mem.state["file_summaries"] == {}


def test_memory_remember_forget_sqlite_sections(tmp_path, monkeypatch):
    temp_db = tmp_path / "agent_memory.db"
    monkeypatch.setattr(paths, "MEMORY_DB_FILE", str(temp_db))

    mem = AgentMemory()
    mem.remember("x", "123", section="key_findings")
    assert mem.state["key_findings"]["x"] == "123"
    mem.forget("x", section="key_findings")
    assert "x" not in mem.state["key_findings"]


def test_memory_initialize_fails_closed_on_corrupt_database_and_can_retry(
    tmp_path,
):
    database = tmp_path / "agent_memory.db"
    database.write_bytes(b"not-a-sqlite-database")
    memory = AgentMemory(
        db_path=database,
        default_file=tmp_path / "agent_memory.json",
        backup_dir=tmp_path / "backups",
    )

    with pytest.raises(MemoryDatabaseError):
        memory.initialize()

    assert memory._initialized is False
    assert memory.state["key_findings"] == {}
    database.unlink()

    memory.initialize()
    memory.remember("retry", "ok")

    assert memory._initialized is True
    assert memory.state["key_findings"]["retry"] == "ok"


def test_strict_memory_restore_rejects_corrupt_json_without_mutating_state(
    tmp_path,
):
    target = tmp_path / "agent_memory.json"
    target.write_text('{"notes": ', encoding="utf-8")
    memory = AgentMemory(
        db_path=tmp_path / "agent_memory.db",
        default_file=target,
        backup_dir=tmp_path / "backups",
    )
    memory.state["notes"]["existing"] = "keep"

    with pytest.raises(MemoryLoadError):
        memory.restore_from_file()

    assert memory.state["notes"] == {"existing": "keep"}
    assert memory.load_from_file().startswith("Erro ao carregar memória:")


def test_sqlite_connections_are_closed_deterministically(tmp_path, monkeypatch):
    from agent.memory import memory as memory_module

    real_connect = memory_module.sqlite3.connect
    connections = []

    class TrackedConnection:
        def __init__(self, connection):
            self.connection = connection
            self.closed = False

        def __getattr__(self, name):
            return getattr(self.connection, name)

        def close(self):
            self.connection.close()
            self.closed = True

    def tracked_connect(*args, **kwargs):
        connection = TrackedConnection(real_connect(*args, **kwargs))
        connections.append(connection)
        return connection

    monkeypatch.setattr(memory_module.sqlite3, "connect", tracked_connect)
    memory = AgentMemory(
        db_path=tmp_path / "agent_memory.db",
        default_file=tmp_path / "agent_memory.json",
        backup_dir=tmp_path / "backups",
    )

    memory.initialize()
    memory.remember("finding", "value")
    memory.forget("finding")
    memory.clear()

    assert connections
    assert all(connection.closed for connection in connections)


def test_tool_executor_summary_survives_memory_restart(tmp_path, monkeypatch):
    source = tmp_path / "sample.py"
    source.write_text("value = 1\n" * 40, encoding="utf-8")
    database = tmp_path / "agent_memory.db"
    memory = AgentMemory(
        db_path=database,
        default_file=tmp_path / "agent_memory.json",
        backup_dir=tmp_path / "backups",
    )
    orchestrator = SimpleNamespace(
        agent_state=SimpleNamespace(memory=memory),
        skills={},
    )
    executor = ToolExecutor(
        orchestrator,
        path_resolver=lambda _path: source,
    )
    monkeypatch.setattr(
        executor,
        "summarize_text",
        lambda _text, context="": "resumo durável",
    )

    executor.maybe_summarize_and_store(
        "file_reader",
        {"file_path": "sample.py"},
        {"ok": True, "done": True, "data": source.read_text(encoding="utf-8")},
    )

    reloaded = AgentMemory(
        db_path=database,
        default_file=tmp_path / "agent_memory.json",
        backup_dir=tmp_path / "backups",
    )
    reloaded.initialize()

    assert reloaded.state["file_summaries"]["sample.py"] == "resumo durável"


def test_memory_clear_rolls_back_and_preserves_state_on_sqlite_failure(tmp_path):
    database = tmp_path / "agent_memory.db"
    memory = AgentMemory(
        db_path=database,
        default_file=tmp_path / "agent_memory.json",
        backup_dir=tmp_path / "backups",
    )
    memory.remember("finding", "value")
    memory.remember("sample.py", "summary", section="file_summaries")
    memory.state["notes"]["local"] = "keep"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TRIGGER reject_summary_clear
            BEFORE DELETE ON file_summaries
            BEGIN
                SELECT RAISE(ABORT, 'falha injetada');
            END
            """
        )

    with pytest.raises(
        MemoryDatabaseError,
        match="Falha ao limpar a memória no SQLite: falha injetada",
    ):
        memory.clear()

    assert memory.state["key_findings"]["finding"] == "value"
    assert memory.state["file_summaries"]["sample.py"] == "summary"
    assert memory.state["notes"]["local"] == "keep"

    reloaded = AgentMemory(
        db_path=database,
        default_file=tmp_path / "agent_memory.json",
        backup_dir=tmp_path / "backups",
    )
    reloaded.initialize()
    assert reloaded.state["key_findings"]["finding"] == "value"
    assert reloaded.state["file_summaries"]["sample.py"] == "summary"


def test_memory_persistence_flushes_before_atomic_replace(tmp_path, monkeypatch):
    target = tmp_path / "agent_memory.json"
    memory = AgentMemory(
        default_file=target,
        db_path=tmp_path / "agent_memory.db",
        backup_dir=tmp_path / "backups",
    )
    calls: list[str] = []
    original_fsync = json_persistence.os.fsync
    original_replace = json_persistence.os.replace

    def observed_fsync(file_descriptor):
        calls.append("fsync")
        original_fsync(file_descriptor)

    def observed_replace(source, destination):
        calls.append("replace")
        original_replace(source, destination)

    monkeypatch.setattr(json_persistence.os, "fsync", observed_fsync)
    monkeypatch.setattr(json_persistence.os, "replace", observed_replace)

    memory.state["notes"] = {"status": "persistido"}
    memory.persist_to_file()

    assert calls == ["fsync", "replace"]
    assert json.loads(target.read_text(encoding="utf-8"))["notes"] == {
        "status": "persistido"
    }


def test_failed_atomic_replace_preserves_previous_memory_and_removes_temp(
    tmp_path,
    monkeypatch,
):
    target = tmp_path / "agent_memory.json"
    previous = {"notes": {"status": "anterior"}}
    target.write_text(json.dumps(previous), encoding="utf-8")
    memory = AgentMemory(
        default_file=target,
        db_path=tmp_path / "agent_memory.db",
        backup_dir=tmp_path / "backups",
    )
    memory.state["notes"] = {"status": "novo"}

    def fail_replace(source, destination):
        del source, destination
        raise OSError("falha injetada no replace")

    monkeypatch.setattr(json_persistence.os, "replace", fail_replace)

    with pytest.raises(AtomicJsonWriteError, match="falha injetada"):
        memory.persist_to_file()

    assert json.loads(target.read_text(encoding="utf-8")) == previous
    assert list(tmp_path.glob("*.tmp")) == []
    assert list(tmp_path.glob(".*.tmp")) == []
    assert memory.save_to_file().startswith("Erro ao salvar memória:")

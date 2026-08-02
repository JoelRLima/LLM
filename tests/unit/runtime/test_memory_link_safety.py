import json
import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.memory import memory as memory_module
from agent.memory import path_safety
from agent.memory.json_persistence import AtomicJsonWriteError
from agent.memory.memory import AgentMemory, MemoryDatabaseError, MemoryLoadError


def _symlink_or_skip(
    link: Path,
    target: Path,
    *,
    target_is_directory: bool = False,
) -> None:
    try:
        link.symlink_to(target, target_is_directory=target_is_directory)
    except OSError as exc:
        if os.name == "nt":
            pytest.skip(f"Criação de link não permitida neste Windows: {exc}")
        raise


def test_json_memory_rejects_final_symlink_for_read_and_write(tmp_path: Path) -> None:
    outside = tmp_path / "outside.json"
    original = {"notes": {"outside": "inalterado"}}
    outside.write_text(json.dumps(original), encoding="utf-8")
    linked_memory = tmp_path / "agent_memory.json"
    _symlink_or_skip(linked_memory, outside)
    memory = AgentMemory(
        db_path=tmp_path / "agent_memory.db",
        default_file=linked_memory,
        backup_dir=tmp_path / "backups",
    )

    with pytest.raises(MemoryLoadError, match="links não são aceitos"):
        memory.restore_from_file()

    memory.state["notes"] = {"outside": "sobrescrito"}
    with pytest.raises(AtomicJsonWriteError, match="links não são aceitos"):
        memory.persist_to_file()

    assert json.loads(outside.read_text(encoding="utf-8")) == original


def test_sqlite_memory_rejects_final_symlink_before_connecting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outside = tmp_path / "outside.db"
    outside.write_bytes(b"nao deve ser aberto")
    linked_database = tmp_path / "agent_memory.db"
    _symlink_or_skip(linked_database, outside)
    memory = AgentMemory(
        db_path=linked_database,
        default_file=tmp_path / "agent_memory.json",
        backup_dir=tmp_path / "backups",
    )

    def unexpected_connect(*args: object, **kwargs: object) -> None:
        del args, kwargs
        pytest.fail("sqlite3.connect não deveria receber um caminho link-like")

    monkeypatch.setattr(memory_module.sqlite3, "connect", unexpected_connect)

    with pytest.raises(MemoryDatabaseError, match="SQLite de memória inseguro"):
        memory.initialize()

    assert outside.read_bytes() == b"nao deve ser aberto"


def test_linked_backup_directory_never_receives_copies(tmp_path: Path) -> None:
    memory_file = tmp_path / "agent_memory.json"
    memory_file.write_text('{"notes": {"version": "anterior"}}', encoding="utf-8")
    outside_backups = tmp_path / "outside-backups"
    outside_backups.mkdir()
    linked_backups = tmp_path / "memory_backups"
    _symlink_or_skip(
        linked_backups,
        outside_backups,
        target_is_directory=True,
    )
    memory = AgentMemory(
        db_path=tmp_path / "agent_memory.db",
        default_file=memory_file,
        backup_dir=linked_backups,
    )
    memory.state["notes"] = {"version": "nova"}

    memory.persist_to_file()

    assert list(outside_backups.iterdir()) == []
    assert json.loads(memory_file.read_text(encoding="utf-8"))["notes"] == {
        "version": "nova"
    }


def test_linked_ancestor_remains_a_valid_configurable_app_path(
    tmp_path: Path,
) -> None:
    real_data = tmp_path / "real-data"
    real_data.mkdir()
    linked_ancestor = tmp_path / "configured-data"
    _symlink_or_skip(
        linked_ancestor,
        real_data,
        target_is_directory=True,
    )
    memory = AgentMemory(
        db_path=linked_ancestor / "agent_memory.db",
        default_file=linked_ancestor / "agent_memory.json",
        backup_dir=linked_ancestor / "memory_backups",
    )
    memory.state["notes"] = {"configured": True}

    memory.initialize()
    memory.persist_to_file()
    memory.restore_from_file()

    assert memory.state["notes"] == {"configured": True}
    assert (real_data / "agent_memory.db").is_file()
    assert (real_data / "agent_memory.json").is_file()


def test_windows_reparse_point_metadata_is_treated_as_link_like(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metadata = SimpleNamespace(
        st_mode=stat.S_IFDIR,
        st_file_attributes=0x0400,
    )
    monkeypatch.setattr(path_safety.os, "lstat", lambda _path: metadata)

    with pytest.raises(path_safety.LinkLikePathError, match="reparse"):
        path_safety.reject_link_like(tmp_path / "junction")

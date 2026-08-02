from pathlib import Path

from agent.memory.memory import AgentMemory
from agent.skills.session_memory import SessionMemorySkill
from agent.state import AgentState


class DummyOrchestrator:
    def __init__(self):
        self.agent_state = AgentState()

    def remember(self, key: str, value: str, section: str = "key_findings") -> None:
        self.agent_state.memory.remember(key, value, section)

    def forget(self, key: str) -> None:
        self.agent_state.memory.forget(key)


def test_session_memory_set_get_delete_keys(tmp_path: Path, monkeypatch):
    from agent.memory import memory as memory_module

    monkeypatch.setattr(memory_module.paths, "MEMORY_DB_FILE", str(tmp_path / "agent_memory.db"))
    orch = DummyOrchestrator()
    orch.agent_state.memory = memory_module.AgentMemory()
    skill = SessionMemorySkill(orchestrator=orch)

    result = skill.execute({"action": "set", "key": "x", "value": "1"})
    assert result["ok"] is True
    assert orch.agent_state.memory.state["key_findings"]["x"] == "1"

    result = skill.execute({"action": "get", "key": "x"})
    assert result["ok"] is True
    assert result["data"] == "1"

    result = skill.execute({"action": "keys"})
    assert result["ok"] is True
    assert result["data"] == ["x"]

    result = skill.execute({"action": "delete", "key": "x"})
    assert result["ok"] is True
    assert "x" not in orch.agent_state.memory.state["key_findings"]


def test_session_memory_keys_empty(tmp_path: Path, monkeypatch):
    from agent.memory import memory as memory_module

    monkeypatch.setattr(memory_module.paths, "MEMORY_DB_FILE", str(tmp_path / "agent_memory.db"))
    orch = DummyOrchestrator()
    orch.agent_state.memory = memory_module.AgentMemory()
    skill = SessionMemorySkill(orchestrator=orch)

    result = skill.execute({"action": "keys"})
    assert result["ok"] is True
    assert result["data"] == []


def test_session_memory_reports_sqlite_insert_failure(
    tmp_path: Path,
    monkeypatch,
):
    from agent.memory import memory as memory_module

    memory = AgentMemory(
        db_path=tmp_path / "agent_memory.db",
        default_file=tmp_path / "agent_memory.json",
        backup_dir=tmp_path / "backups",
    )
    memory.initialize()
    orchestrator = DummyOrchestrator()
    orchestrator.agent_state.memory = memory
    skill = SessionMemorySkill(orchestrator=orchestrator)

    def fail_connect(*args, **kwargs):
        del args, kwargs
        raise memory_module.sqlite3.OperationalError("falha injetada")

    monkeypatch.setattr(memory_module.sqlite3, "connect", fail_connect)

    result = skill.execute({"action": "set", "key": "x", "value": "1"})

    assert result["ok"] is False
    assert result["done"] is True
    assert "Falha ao persistir key_findings" in result["error"]
    assert "x" not in memory.state["key_findings"]


def test_session_memory_reports_sqlite_delete_failure_without_losing_state(
    tmp_path: Path,
    monkeypatch,
):
    from agent.memory import memory as memory_module

    memory = AgentMemory(
        db_path=tmp_path / "agent_memory.db",
        default_file=tmp_path / "agent_memory.json",
        backup_dir=tmp_path / "backups",
    )
    memory.remember("x", "1")
    orchestrator = DummyOrchestrator()
    orchestrator.agent_state.memory = memory
    skill = SessionMemorySkill(orchestrator=orchestrator)

    def fail_connect(*args, **kwargs):
        del args, kwargs
        raise memory_module.sqlite3.OperationalError("falha injetada")

    monkeypatch.setattr(memory_module.sqlite3, "connect", fail_connect)

    result = skill.execute({"action": "delete", "key": "x"})

    assert result["ok"] is False
    assert result["done"] is True
    assert "Falha ao remover key_findings" in result["error"]
    assert memory.state["key_findings"]["x"] == "1"

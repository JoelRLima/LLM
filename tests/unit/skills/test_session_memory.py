from pathlib import Path

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

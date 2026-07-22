import json

from agent.memory.memory import AgentMemory
from agent.runtime import paths


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

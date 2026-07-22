from agent.skills.file_writer import FileWriterSkill


def test_file_writer_blocks_agent_directory(tmp_path, monkeypatch):
    base_dir = tmp_path
    writer = FileWriterSkill(base_dir=str(base_dir))

    target = base_dir / "agent" / "orchestrator.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("print('x')", encoding="utf-8")

    ok, reason = writer._is_safe(target)
    assert ok is False
    assert "core do agente" in reason.lower()


def test_file_writer_allows_non_agent_file(tmp_path):
    base_dir = tmp_path
    writer = FileWriterSkill(base_dir=str(base_dir))

    target = base_dir / "README.md"
    target.write_text("ok", encoding="utf-8")

    ok, reason = writer._is_safe(target)
    assert ok is True
    assert reason == ""


def test_file_writer_respects_allowlist(tmp_path, monkeypatch):
    base_dir = tmp_path
    writer = FileWriterSkill(base_dir=str(base_dir))

    monkeypatch.setattr("agent.skills.file_writer.AGENT_EDIT_ALLOWLIST", {"agent/orchestrator.py"})
    target = base_dir / "agent" / "orchestrator.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("print('x')", encoding="utf-8")

    ok, reason = writer._is_safe(target)
    assert ok is True
    assert reason == ""


def test_ast_patch_commits_to_original_file(tmp_path, monkeypatch):
    target = tmp_path / "sample.py"
    target.write_text("def value():\n    return 1\n", encoding="utf-8")
    writer = FileWriterSkill(base_dir=str(tmp_path))
    monkeypatch.setattr("agent.skills.file_writer._is_auto_confirm", lambda: True)

    result = writer.execute(
        {
            "action": "ast_patch",
            "file_path": "sample.py",
            "target": "value",
            "new_code": "def value():\n    return 2",
        }
    )

    assert result["ok"] is True
    assert target.read_text(encoding="utf-8") == "def value():\n    return 2\n"
    assert not list(tmp_path.rglob("*.ast_bak"))


def test_ast_patch_preserves_nested_indentation(tmp_path, monkeypatch):
    target = tmp_path / "sample.py"
    target.write_text(
        "class Example:\n    def value(self):\n        return 1\n",
        encoding="utf-8",
    )
    writer = FileWriterSkill(base_dir=str(tmp_path))
    monkeypatch.setattr("agent.skills.file_writer._is_auto_confirm", lambda: True)

    result = writer.execute(
        {
            "action": "ast_patch",
            "file_path": "sample.py",
            "target": "value",
            "new_code": "def value(self):\n    if True:\n        return 2",
        }
    )

    assert result["ok"] is True
    compile(target.read_text(encoding="utf-8"), str(target), "exec")

from agent.approval import AutoApprove, RequireExplicitApproval
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


def test_ast_patch_commits_to_original_file(tmp_path):
    target = tmp_path / "sample.py"
    target.write_text("def value():\n    return 1\n", encoding="utf-8")
    writer = FileWriterSkill(base_dir=str(tmp_path), auto_confirm=True)

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


def test_ast_patch_preserves_nested_indentation(tmp_path):
    target = tmp_path / "sample.py"
    target.write_text(
        "class Example:\n    def value(self):\n        return 1\n",
        encoding="utf-8",
    )
    writer = FileWriterSkill(base_dir=str(tmp_path), config={"auto_confirm": True})

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


def test_file_writer_uses_injected_scratch_directory(tmp_path):
    workspace = tmp_path / "workspace"
    scratch = tmp_path / "state" / "scratch"
    workspace.mkdir()
    target = workspace / "sample.py"
    target.write_text("value = 1\n", encoding="utf-8")
    writer = FileWriterSkill(
        base_dir=workspace,
        scratch_dir=scratch,
        auto_confirm=True,
    )

    result = writer.execute(
        {
            "action": "write",
            "file_path": "sample.py",
            "content": "value = 2\n",
        }
    )

    assert result["ok"] is True
    assert target.read_text(encoding="utf-8") == "value = 2\n"
    assert scratch.is_dir()
    assert not (workspace / ".temp_analysis").exists()


def test_explicit_auto_confirm_overrides_config(tmp_path):
    writer = FileWriterSkill(
        base_dir=tmp_path,
        config={"auto_confirm": True},
        auto_confirm=False,
    )

    assert writer._is_auto_confirm() is False


def test_file_writer_requires_approval_without_reading_stdin(tmp_path, monkeypatch):
    target = tmp_path / "pending.txt"
    writer = FileWriterSkill(
        base_dir=tmp_path,
        approval_policy=RequireExplicitApproval(),
    )

    def forbidden_input(*args, **kwargs):
        raise AssertionError(f"input() não pode ser usado: {args!r} {kwargs!r}")

    monkeypatch.setattr("builtins.input", forbidden_input)

    result = writer.execute(
        {
            "action": "write",
            "file_path": "pending.txt",
            "content": "não aplicar\n",
        }
    )

    assert result["ok"] is False
    assert result["status"] == "blocked"
    assert result["error"] == "confirmation_required"
    assert not target.exists()


def test_injected_auto_approval_applies_without_reading_stdin(tmp_path, monkeypatch):
    target = tmp_path / "approved.txt"
    writer = FileWriterSkill(
        base_dir=tmp_path,
        approval_policy=AutoApprove(),
    )
    monkeypatch.setattr(
        "builtins.input",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("input() não pode ser usado")
        ),
    )

    result = writer.execute(
        {
            "action": "write",
            "file_path": "approved.txt",
            "content": "aplicado\n",
        }
    )

    assert result["ok"] is True
    assert target.read_text(encoding="utf-8") == "aplicado\n"

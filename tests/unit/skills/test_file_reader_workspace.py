from agent.skills.file_reader import FileReaderSkill


def test_file_reader_uses_injected_scratch_directory(tmp_path):
    workspace = tmp_path / "workspace"
    scratch = tmp_path / "state" / "scratch"
    workspace.mkdir()
    target = workspace / "large.txt"
    target.write_text("primeira linha\nsegunda linha\n", encoding="utf-8")
    reader = FileReaderSkill(
        base_dir=workspace,
        max_chars=1,
        scratch_dir=scratch,
    )

    result = reader.execute({"file_path": "large.txt"})

    assert result["ok"] is True
    assert (scratch / "large.txt").read_text(encoding="utf-8") == target.read_text(
        encoding="utf-8"
    )
    assert not (workspace / ".temp_analysis").exists()


def test_file_reader_prefers_staged_copy_from_injected_scratch(tmp_path):
    workspace = tmp_path / "workspace"
    scratch = tmp_path / "state" / "scratch"
    workspace.mkdir()
    (workspace / "sample.txt").write_text("original", encoding="utf-8")
    staged = scratch / "workspace" / "sample.txt"
    staged.parent.mkdir(parents=True)
    staged.write_text("staged", encoding="utf-8")
    reader = FileReaderSkill(base_dir=workspace, scratch_dir=scratch)

    result = reader.execute({"file_path": "sample.txt"})

    assert result["ok"] is True
    assert result["data"] == "staged"

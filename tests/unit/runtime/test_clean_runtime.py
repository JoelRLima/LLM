from pathlib import Path

from scripts.clean_runtime import apply_cleanup, discover_cleanup


def test_cleanup_archives_state_and_deletes_only_allowlisted_caches(tmp_path: Path) -> None:
    (tmp_path / "agent_memory.json").write_text("{}", encoding="utf-8")
    cache = tmp_path / ".pytest_tmp"
    cache.mkdir()
    (cache / "cache.txt").write_text("temporary", encoding="utf-8")
    config = tmp_path / "config.json"
    config.write_text('{"secret": true}', encoding="utf-8")

    items = discover_cleanup(tmp_path)
    messages = apply_cleanup(items, tmp_path / "runtime" / "archive" / "test")

    assert any(message.startswith("archived") for message in messages)
    assert any(message.startswith("deleted") for message in messages)
    assert (tmp_path / "runtime" / "archive" / "test" / "agent_memory.json").exists()
    assert not cache.exists()
    assert config.exists()


def test_workspace_temp_requires_explicit_opt_in(tmp_path: Path) -> None:
    workspace = tmp_path / ".temp_analysis"
    workspace.mkdir()

    assert discover_cleanup(tmp_path) == ()
    assert discover_cleanup(tmp_path, include_workspace_temp=True)[0].path == workspace.resolve()

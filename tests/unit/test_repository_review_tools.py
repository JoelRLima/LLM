import json
import zipfile

import pytest

from scripts import repository_checkpoint, repository_inventory


def test_checkpoint_copies_allowlisted_source_without_reading_config(tmp_path, monkeypatch):
    root = tmp_path / "checkout"
    (root / "src").mkdir(parents=True)
    (root / "src/module.py").write_text("original source")
    (root / "config.json").write_text("private configuration")

    def git(_root, *args):
        if args[0] == "ls-files":
            return b"src/module.py\0src/deleted.py\0config.json\0"
        return b"head\n" if args[0] == "rev-parse" else b" M src/module.py\0"

    monkeypatch.setattr(repository_checkpoint, "git", git)
    output = tmp_path / "review"
    document = repository_checkpoint.checkpoint(root, output, snapshot=True)
    assert document["excluded_paths"] == ["config.json"]
    assert document["files"][0] == {"path": "src/deleted.py", "state": "deleted"}
    with zipfile.ZipFile(output / "source.zip") as archive:
        assert archive.namelist() == ["src/module.py"]
        assert archive.read("src/module.py") == b"original source"
    assert json.loads((output / "manifest.json").read_text())["source_sha256"] == document["source_sha256"]
    with pytest.raises(FileExistsError):
        repository_checkpoint.checkpoint(root, output)


def test_cache_cleanup_refuses_unlisted_and_linked_targets(tmp_path, monkeypatch):
    protected = tmp_path / ".workbench-managed"
    protected.mkdir()
    (protected / "keep").write_text("necessary state")
    with pytest.raises(ValueError, match="Unsafe"):
        repository_inventory.remove_cache(tmp_path, protected)
    assert (protected / "keep").exists()
    cache = tmp_path / ".ruff_cache"
    cache.mkdir()
    (cache / "disposable").write_text("cache")
    monkeypatch.setattr(repository_inventory, "linked", lambda path: True)
    with pytest.raises(ValueError, match="Unsafe"):
        repository_inventory.remove_cache(tmp_path, cache)
    assert cache.exists()


def test_cache_cleanup_removes_only_known_cache(tmp_path):
    cache = tmp_path / ".pytest_cache"
    cache.mkdir()
    (cache / "data").write_text("disposable")
    repository_inventory.remove_cache(tmp_path, cache)
    assert not cache.exists()
    assert repository_inventory.category(tmp_path / ".venv") == "protected_local_state"
    assert repository_inventory.category(tmp_path / "prototypes") == "experiments_and_historical_evidence"

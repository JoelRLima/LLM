from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import w23_ci_identity


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True,
    )
    return result.stdout.strip()


def _repository(root: Path, *, nested: bool = False) -> Path:
    root.mkdir()
    _git(root, "init", "--initial-branch=main")
    _git(root, "config", "user.name", "W23 test")
    _git(root, "config", "user.email", "w23@example.invalid")
    (root / ".gitattributes").write_text("*.cmd text eol=crlf\n", encoding="ascii")
    file = root / "Workbench.cmd"
    file.write_bytes(b"@echo off\necho stable\n")
    if nested:
        (root / "subdir").mkdir()
        (root / "subdir" / "tracked.txt").write_bytes(b"tracked\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "fixture")
    return file


def _install_link_or_windows_reparse(
    path: Path,
    target: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    directory: bool = False,
) -> None:
    try:
        path.symlink_to(target, target_is_directory=directory)
    except OSError as exc:
        if os.name != "nt" or getattr(exc, "winerror", None) != 1314:
            raise
        original_lstat = Path.lstat
        file_type = stat.S_IFDIR if directory else stat.S_IFLNK
        attributes = 0x400 if directory else 0

        def lstat(candidate: Path) -> os.stat_result:
            if candidate == path:
                return SimpleNamespace(st_mode=file_type, st_file_attributes=attributes)  # type: ignore[return-value]
            return original_lstat(candidate)

        monkeypatch.setattr(Path, "lstat", lstat)


def test_capture_accepts_gitattributes_checkout_normalization(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    file = _repository(root)
    file.write_bytes(b"@echo off\r\necho stable\r\n")

    result = w23_ci_identity.capture(root)

    assert result["source_tree"] == _git(root, "rev-parse", "HEAD^{tree}")
    assert result["head_commit"] == _git(root, "rev-parse", "HEAD")
    assert len(result["files"]) == 2


def test_capture_rejects_real_content_change_after_clean_filter(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    file = _repository(root)
    file.write_bytes(b"@echo off\r\necho altered\r\n")

    with pytest.raises(ValueError, match="changed authenticated bytes: Workbench.cmd"):
        w23_ci_identity.capture(root)


def test_capture_rejects_symlink_in_place_of_tracked_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repo"
    file = _repository(root)
    target = tmp_path / "outside.txt"
    target.write_bytes(b"@echo off\necho stable\n")
    file.unlink()
    _install_link_or_windows_reparse(file, target, monkeypatch)

    with pytest.raises(ValueError, match="symlink or reparse point"):
        w23_ci_identity.capture(root)


def test_capture_rejects_symlinked_parent_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repo"
    _repository(root, nested=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "tracked.txt").write_bytes(b"tracked\n")
    (root / "subdir" / "tracked.txt").unlink()
    (root / "subdir").rmdir()
    _install_link_or_windows_reparse(root / "subdir", outside, monkeypatch, directory=True)

    with pytest.raises(ValueError, match="symlink or reparse point"):
        w23_ci_identity.capture(root)


def test_capture_binds_commit_even_when_tree_is_unchanged(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _repository(root)
    before = w23_ci_identity.capture(root)
    _git(root, "commit", "--allow-empty", "-m", "same tree, different commit")
    after = w23_ci_identity.capture(root)

    assert before["source_tree"] == after["source_tree"]
    assert before["head_commit"] != after["head_commit"]
    with pytest.raises(ValueError, match="tracked source changed during acceptance"):
        w23_ci_identity.assert_unchanged(before, after)


def test_after_capture_detects_checkout_representation_change(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    file = _repository(root)
    file.write_bytes(b"@echo off\r\necho stable\r\n")
    before = w23_ci_identity.capture(root)
    file.write_bytes(b"@echo off\necho stable\n")
    after = w23_ci_identity.capture(root)

    assert before["source_tree"] == after["source_tree"]
    with pytest.raises(ValueError, match="tracked source changed during acceptance"):
        w23_ci_identity.assert_unchanged(before, after)


def test_capture_rejects_file_changed_while_clean_filter_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repo"
    file = _repository(root)
    original_git = w23_ci_identity._git

    def mutate_after_hash(root_arg: Path, *args: str, input: bytes | None = None) -> bytes:
        result = original_git(root_arg, *args, input=input)
        if args[:2] == ("hash-object", "--stdin-paths"):
            file.write_bytes(b"@echo off\r\necho changed during capture\r\n")
        return result

    monkeypatch.setattr(w23_ci_identity, "_git", mutate_after_hash)
    with pytest.raises(ValueError, match="changed while authenticating: Workbench.cmd"):
        w23_ci_identity.capture(root)


def test_capture_rejects_index_not_matching_head_commit(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    file = _repository(root)
    file.write_bytes(b"@echo off\necho staged but uncommitted\n")
    _git(root, "add", "Workbench.cmd")

    with pytest.raises(ValueError, match="index differs from authenticated commit tree"):
        w23_ci_identity.capture(root)

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


def _materialize_tracked_files(root: Path) -> None:
    tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=root)
    # checkout-index can retain files whose cached stat data says up to date.
    # Remove only fixture files so Git must actually materialize every blob.
    for relative in tracked.split(b"\0"):
        if relative:
            (root / relative.decode("utf-8")).unlink()
    _git(root, "--attr-source=HEAD", "checkout-index", "--all")


def _repository(root: Path, *, nested: bool = False) -> Path:
    root.mkdir()
    _git(root, "init", "--initial-branch=main")
    _git(root, "config", "user.name", "W23 test")
    _git(root, "config", "user.email", "w23@example.invalid")
    _git(root, "config", "core.autocrlf", "false")
    _git(root, "config", "core.eol", "lf")
    (root / ".gitattributes").write_bytes(b"*.cmd text eol=crlf\n")
    file = root / "Workbench.cmd"
    file.write_bytes(b"@echo off\necho stable\n")
    if nested:
        (root / "subdir").mkdir()
        (root / "subdir" / "tracked.txt").write_bytes(b"tracked\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "fixture")
    _materialize_tracked_files(root)
    assert (root / ".gitattributes").read_bytes() == b"*.cmd text eol=crlf\n"
    assert file.read_bytes() == b"@echo off\r\necho stable\r\n"
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


def test_capture_rejects_file_changed_while_git_objects_are_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repo"
    file = _repository(root)
    original_git = w23_ci_identity._git

    def mutate_after_object_read(root_arg: Path, *args: str, input: bytes | None = None) -> bytes:
        result = original_git(root_arg, *args, input=input)
        if args[:2] == ("cat-file", "--batch"):
            file.write_bytes(b"@echo off\r\necho changed during capture\r\n")
        return result

    monkeypatch.setattr(w23_ci_identity, "_git", mutate_after_object_read)
    with pytest.raises(ValueError, match="changed while authenticating: Workbench.cmd"):
        w23_ci_identity.capture(root)


def test_capture_rejects_index_not_matching_head_commit(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    file = _repository(root)
    file.write_bytes(b"@echo off\necho staged but uncommitted\n")
    _git(root, "add", "Workbench.cmd")

    with pytest.raises(ValueError, match="index differs from authenticated commit tree"):
        w23_ci_identity.capture(root)


def _historical_repository(root: Path) -> list[Path]:
    _repository(root)
    (root / ".gitattributes").write_bytes(
        b"* text=auto eol=lf\n*.cmd text eol=crlf\n*.bin -text\n*.evidence -text\n",
    )
    _git(root, "add", ".gitattributes")
    payloads = {f"legacy folder/legacy {i:02}.py": b"first\r\nsecond\n" if i % 2 else b"first\r\nsecond\r\n"
                for i in range(46)}
    payloads.update({"normal.py": b"normal\n", "payload.bin": b"\0binary\r\n",
                     "history.evidence": b"exact historical\r\n"})
    # Reproduce objects already stored before normalization rules were applied.
    # Insert their exact blob identities without applying today's clean filter.
    for relative, data in payloads.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    oids = subprocess.check_output(
        ["git", "hash-object", "-w", "--no-filters", "--stdin-paths"], cwd=root,
        input=("\n".join(payloads) + "\n").encode("utf-8"),
    ).decode().splitlines()
    records = "".join("100644 " + oid + "\t" + path + "\0"
                      for path, oid in zip(payloads, oids, strict=True)).encode("utf-8")
    subprocess.run(["git", "update-index", "-z", "--index-info"], cwd=root, input=records,
                   check=True, capture_output=True)
    _git(root, "commit", "-m", "historical blobs with current attributes")
    _materialize_tracked_files(root)
    return [root / relative for relative in payloads]


@pytest.mark.parametrize(("autocrlf", "eol"), [("false", "lf"), ("true", "crlf")])
def test_capture_authenticates_entire_git_checkout_with_historical_blobs(
    tmp_path: Path, autocrlf: str, eol: str,
) -> None:
    root = tmp_path / "repo"
    paths = _historical_repository(root)
    _git(root, "config", "core.autocrlf", autocrlf)
    _git(root, "config", "core.eol", eol)
    _materialize_tracked_files(root)
    legacy = paths[0]
    relative = legacy.relative_to(root).as_posix()
    oid = _git(root, "rev-parse", "HEAD:" + relative)
    cleaned = subprocess.check_output(
        ["git", "hash-object", "--path=" + relative, "--stdin"], cwd=root, input=legacy.read_bytes(),
    ).decode().strip()
    assert cleaned != oid  # This genuine Git checkout fails the old verifier.

    result = w23_ci_identity.capture(root)

    expected = {".gitattributes", "Workbench.cmd"} | {p.relative_to(root).as_posix() for p in paths}
    assert {row["path"] for row in result["files"]} == expected
    assert len(result["files"]) == 51
    assert result["head_commit"] == _git(root, "rev-parse", "HEAD")
    assert (root / "Workbench.cmd").read_bytes() == b"@echo off\r\necho stable\r\n"
    assert (root / "payload.bin").read_bytes() == b"\0binary\r\n"
    assert (root / "history.evidence").read_bytes() == b"exact historical\r\n"


@pytest.mark.parametrize("relative", ["legacy folder/legacy 00.py", "payload.bin", "history.evidence"])
def test_capture_rejects_content_changes_in_historical_text_binary_and_evidence(
    tmp_path: Path, relative: str,
) -> None:
    root = tmp_path / "repo"
    _historical_repository(root)
    path = root / relative
    path.write_bytes(path.read_bytes() + b"unauthorized")

    with pytest.raises(ValueError, match="Git checkout changed authenticated bytes"):
        w23_ci_identity.capture(root)


@pytest.mark.parametrize("relative", ["legacy folder/legacy 00.py", "history.evidence", "normal.py"])
def test_capture_rejects_line_endings_not_produced_by_git_checkout(tmp_path: Path, relative: str) -> None:
    root = tmp_path / "repo"
    _historical_repository(root)
    path = root / relative
    data = path.read_bytes()
    changed = data.replace(b"\r\n", b"\n") if b"\r\n" in data else data.replace(b"\n", b"\r\n")
    assert changed != data
    path.write_bytes(changed)

    with pytest.raises(ValueError, match="Git checkout changed authenticated bytes"):
        w23_ci_identity.capture(root)


def test_capture_does_not_trust_untracked_worktree_attributes(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _repository(root)
    nested = root / "nested"
    nested.mkdir()
    file = nested / "tracked.txt"
    file.write_bytes(b"$Id$\n")
    _git(root, "add", "nested/tracked.txt")
    _git(root, "commit", "-m", "tracked ident token without ident attribute")
    (nested / ".gitattributes").write_bytes(b"*.txt ident\n")
    oid = _git(root, "rev-parse", "HEAD:nested/tracked.txt")
    unauthorized = subprocess.check_output(
        ["git", "cat-file", "--filters", "--path=nested/tracked.txt", oid], cwd=root,
    )
    assert unauthorized != file.read_bytes()
    file.write_bytes(unauthorized)

    with pytest.raises(ValueError, match="Git checkout changed authenticated bytes: nested/tracked.txt"):
        w23_ci_identity.capture(root)


def test_capture_rejects_blob_bytes_that_do_not_match_requested_object(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repo"
    _repository(root)
    original_git = w23_ci_identity._git

    def corrupt_blob(root_arg: Path, *args: str, input: bytes | None = None) -> bytes:
        result = original_git(root_arg, *args, input=input)
        if args[:2] == ("cat-file", "--batch"):
            return result.replace(b"*.cmd", b"?.cmd", 1)
        return result

    monkeypatch.setattr(w23_ci_identity, "_git", corrupt_blob)
    with pytest.raises(ValueError, match="Git blob bytes differ from authenticated object identity"):
        w23_ci_identity.capture(root)

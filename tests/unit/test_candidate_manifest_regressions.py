"""Portable candidate regressions without commits, lifecycle or model calls."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from typing import Any

import pytest
from test_candidate_manifest import _repo

from distribution import candidate_snapshot
from distribution.candidate_manifest import (
    CandidateManifestError,
    _validate_file_namespace,
    canonical_manifest_bytes,
    validate_repo_path,
)


@pytest.mark.parametrize("mask", [0x00000002, 0x00000004])
def test_windows_ancestor_add_rights_are_not_replacement_rights(mask: int) -> None:
    """Policy-only simulation; this does not create or inspect a native Windows ACL."""
    assert not candidate_snapshot._windows_acl_mask_is_unsafe(mask, allow_child_creation=True)
    assert candidate_snapshot._windows_acl_mask_is_unsafe(mask, allow_child_creation=False)


@pytest.mark.parametrize("mask", [0x00000040, 0x00010000, 0x00040000, 0x00080000, 0x10000000, 0x40000000])
def test_windows_ancestor_substitution_and_acl_rights_remain_unsafe(mask: int) -> None:
    assert candidate_snapshot._windows_acl_mask_is_unsafe(mask, allow_child_creation=True)
    assert candidate_snapshot._windows_acl_mask_is_unsafe(mask, allow_child_creation=False)


RESERVED = ["CON", "PRN", "AUX", "NUL"] + [f"{prefix}{digit}" for prefix in ("COM", "LPT") for digit in "123456789¹²³"]


@pytest.mark.parametrize("name", RESERVED)
@pytest.mark.parametrize("shape", ["{}", "{}.txt", "safe/{}.log/child"])
def test_reserved_device_component_is_rejected(name: str, shape: str) -> None:
    with pytest.raises(CandidateManifestError, match="reserved Windows"):
        validate_repo_path(shape.format(name.lower()))


@pytest.mark.parametrize("path", ["ação/日本語.txt", "COM10", "COM⁴.txt", "COM¹x", "CON¹.txt", "COM1¹.txt", "é/δ.log"])
def test_legitimate_unicode_and_non_devices_remain_accepted(path: str) -> None:
    assert validate_repo_path(path) == path


@pytest.mark.parametrize("paths", [("A/x", "a/y"), ("A/a/x", "a/b/y"), ("a", "a/b")])
def test_file_namespace_rejects_directory_spelling_and_file_parent_collision(paths: tuple[str, str]) -> None:
    with pytest.raises(CandidateManifestError):
        _validate_file_namespace([{"path": path} for path in paths])


def test_file_namespace_accepts_consistent_unicode_directories() -> None:
    _validate_file_namespace([{"path": path} for path in ("A/x", "A/y", "ação/日本語")])


def _manifest(tmp_path: Path, object_format: str) -> tuple[Path, dict[str, Any]]:
    repository = tmp_path / "repository"
    repository.mkdir()
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")

    def git(*args: str, data: bytes | None = None) -> str:
        return subprocess.run(["git", *args], cwd=repository, env=env, input=data,
                              capture_output=True, check=True, timeout=8).stdout.decode().strip()

    git("init", "-q", "--template=", f"--object-format={object_format}")
    payload = b"CRLF\r\n\x00\xffbinary"
    oid = git("hash-object", "-w", "--stdin", "--no-filters", data=payload)
    git("update-index", "--index-info", data=f"100755 {oid}\tbytes.bin\n".encode())
    tree = git("write-tree")
    materialized = tmp_path / "materialized"
    materialized.mkdir()
    target = materialized / "bytes.bin"
    target.write_bytes(payload)
    if os.name != "nt":
        target.chmod(0o755)
    length = 40 if object_format == "sha1" else 64
    return materialized, {
        "schema_version": 1, "kind": "EXACT_BYTE_REPOSITORY_CANDIDATE", "unit": "DISPOSABLE_REGRESSION",
        "object_format": object_format, "base": {"head": "1" * length, "tree": tree}, "source_tree": tree,
        "files": [{"path": "bytes.bin", "mode": "100755", "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}],
        "overlay": [], "lineage": {
            "authority_predecessor": {"path": "authority.json", "sha256": "0" * 64},
            "inherited_observation": {"path": "inventory.json", "sha256": "0" * 64}, "acceptance_records": [],
        }, "selected_evidence": [], "workspace_context": {"protected_files": [], "operational_roots": []},
    }


@pytest.mark.parametrize("object_format", ["sha1", "sha256"])
def test_materialized_verifier_preserves_real_git_format_and_rejects_wrong_oid(
    tmp_path: Path, object_format: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, manifest = _manifest(tmp_path, object_format)
    original_run = subprocess.run

    def bounded_run(*args: Any, **kwargs: Any) -> Any:
        kwargs["timeout"] = 8
        return original_run(*args, **kwargs)

    monkeypatch.setattr(subprocess, "run", bounded_run)
    repository = tmp_path / "repository" / ".git"
    before = {p.relative_to(repository).as_posix(): p.read_bytes() for p in repository.rglob("*") if p.is_file()}
    encoded = canonical_manifest_bytes(manifest)
    snapshot = candidate_snapshot.verify_materialized(root, encoded, hashlib.sha256(encoded).hexdigest())
    assert snapshot.source_tree == manifest["source_tree"]
    assert (root / "bytes.bin").read_bytes() == b"CRLF\r\n\x00\xffbinary"
    manifest["source_tree"] = "0" * len(manifest["source_tree"])
    wrong = canonical_manifest_bytes(manifest)
    with pytest.raises(candidate_snapshot.CandidateSnapshotError, match="source_tree"):
        candidate_snapshot.verify_materialized(root, wrong, hashlib.sha256(wrong).hexdigest())
    assert before == {p.relative_to(repository).as_posix(): p.read_bytes() for p in repository.rglob("*") if p.is_file()}


def test_materialized_verifier_checks_format_after_initialization(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, manifest = _manifest(tmp_path, "sha1")
    original_git = candidate_snapshot._git

    def wrong_format(git_root: Path, args: list[str], **kwargs: Any) -> bytes:
        if args == ["rev-parse", "--show-object-format"]:
            return b"sha256\n"
        return original_git(git_root, args, **kwargs)

    monkeypatch.setattr(candidate_snapshot, "_git", wrong_format)
    encoded = canonical_manifest_bytes(manifest)
    with pytest.raises(candidate_snapshot.CandidateSnapshotError, match="object format"):
        candidate_snapshot.verify_materialized(root, encoded, hashlib.sha256(encoded).hexdigest())

def _candidate_stages(parent: Path) -> list[Path]:
    return sorted(parent.glob(".cand03-*"))


def test_workspace_materializer_supports_sha256_and_executable_git_mode(tmp_path: Path) -> None:
    root, encoded, digest = _repo(tmp_path, object_format="sha256", executable=True)
    destination = tmp_path / "sha256-materialized"

    snapshot = candidate_snapshot.verify_workspace(root, encoded, digest, destination)

    assert snapshot.manifest["object_format"] == "sha256"
    assert snapshot.materialized_root == destination.absolute()
    assert (destination / "src/app.py").read_bytes() == b"line one\r\nline two\r\n"
    assert (destination / "tools/check.py").read_bytes() == b"print('ok')\n"
    assert snapshot.manifest["files"][-1]["mode"] == "100755"
    if os.name != "nt":
        assert (destination / "tools/check.py").stat().st_mode & 0o111


@pytest.mark.parametrize("state", ["empty_directory", "nonempty_directory", "regular_file"])
def test_existing_destination_is_rejected_without_mutation(tmp_path: Path, state: str) -> None:
    root, encoded, digest = _repo(tmp_path)
    destination = tmp_path / "destination"
    if state.endswith("directory"):
        destination.mkdir()
        original_identity = (destination.stat().st_dev, destination.stat().st_ino)
        if state == "nonempty_directory":
            (destination / "sentinel.bin").write_bytes(b"keep exact bytes")
    else:
        destination.write_bytes(b"keep exact bytes")
        original_bytes = destination.read_bytes()

    with pytest.raises(candidate_snapshot.CandidateSnapshotError, match="must not already exist"):
        candidate_snapshot.verify_workspace(root, encoded, digest, destination)

    if state.endswith("directory"):
        assert (destination.stat().st_dev, destination.stat().st_ino) == original_identity
        assert sorted(path.name for path in destination.iterdir()) == (
            ["sentinel.bin"] if state == "nonempty_directory" else []
        )
        if state == "nonempty_directory":
            assert (destination / "sentinel.bin").read_bytes() == b"keep exact bytes"
    else:
        assert destination.read_bytes() == original_bytes
    assert _candidate_stages(tmp_path) == []


@pytest.mark.parametrize("target_exists", [False, True])
def test_destination_symlink_is_rejected_without_following_or_mutating_target(
    tmp_path: Path, target_exists: bool,
) -> None:
    root, encoded, digest = _repo(tmp_path)
    destination = tmp_path / "destination"
    target = tmp_path / "link-target"
    if target_exists:
        target.write_bytes(b"protected target")
    try:
        destination.symlink_to(target, target_is_directory=False)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")

    with pytest.raises(candidate_snapshot.CandidateSnapshotError, match="must not already exist"):
        candidate_snapshot.verify_workspace(root, encoded, digest, destination)

    assert destination.is_symlink()
    assert target.exists() is target_exists
    if target_exists:
        assert target.read_bytes() == b"protected target"
    assert _candidate_stages(tmp_path) == []


def test_symlink_parent_is_rejected_without_writing_through_it(tmp_path: Path) -> None:
    root, encoded, digest = _repo(tmp_path)
    real_parent = tmp_path / "real-parent"
    real_parent.mkdir()
    linked_parent = tmp_path / "linked-parent"
    try:
        linked_parent.symlink_to(real_parent, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlink creation is unavailable: {exc}")

    with pytest.raises(candidate_snapshot.CandidateSnapshotError):
        candidate_snapshot.verify_workspace(root, encoded, digest, linked_parent / "destination")

    assert list(real_parent.iterdir()) == []
    assert _candidate_stages(real_parent) == []


def test_false_source_tree_is_rejected_before_destination_publication(tmp_path: Path) -> None:
    root, encoded, _ = _repo(tmp_path)
    document = json.loads(encoded)
    document["source_tree"] = "0" * len(document["source_tree"])
    invalid = canonical_manifest_bytes(document)
    digest = hashlib.sha256(invalid).hexdigest()
    destination = tmp_path / "destination"

    with pytest.raises(candidate_snapshot.CandidateSnapshotError, match="source_tree"):
        candidate_snapshot.verify_workspace(root, invalid, digest, destination)

    assert not destination.exists()
    assert _candidate_stages(tmp_path) == []


def test_partial_staging_write_is_cleaned_after_controlled_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, encoded, digest = _repo(tmp_path)
    destination = tmp_path / "destination"

    def partial_write(
        git_root: Path, source_root: Path, manifest: dict[str, Any], env: dict[str, str],
        stage_root: Path | None = None,
    ) -> str:
        assert stage_root is not None
        (stage_root / "partial.bin").write_bytes(b"partial")
        raise candidate_snapshot.CandidateSnapshotError("injected staging write failure")

    monkeypatch.setattr(candidate_snapshot, "_construct_tree", partial_write)
    with pytest.raises(candidate_snapshot.CandidateSnapshotError, match="injected staging write failure"):
        candidate_snapshot.verify_workspace(root, encoded, digest, destination)

    assert not destination.exists()
    assert _candidate_stages(tmp_path) == []


def test_git_write_tree_failure_cleans_private_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, encoded, digest = _repo(tmp_path)
    destination = tmp_path / "destination"
    original_git = candidate_snapshot._git

    def fail_write_tree(git_root: Path, args: list[str], **kwargs: Any) -> bytes:
        if args == ["write-tree"]:
            raise candidate_snapshot.CandidateSnapshotError("injected write-tree failure")
        return original_git(git_root, args, **kwargs)

    monkeypatch.setattr(candidate_snapshot, "_git", fail_write_tree)
    with pytest.raises(candidate_snapshot.CandidateSnapshotError, match="write-tree"):
        candidate_snapshot.verify_workspace(root, encoded, digest, destination)

    assert not destination.exists()
    assert _candidate_stages(tmp_path) == []


def test_private_tree_verification_failure_cleans_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, encoded, digest = _repo(tmp_path)
    destination = tmp_path / "destination"

    def reject_private_tree(_root: Path, _manifest: dict[str, Any]) -> None:
        raise candidate_snapshot.CandidateSnapshotError("injected private verification failure")

    monkeypatch.setattr(candidate_snapshot, "_verify_private_tree", reject_private_tree)
    with pytest.raises(candidate_snapshot.CandidateSnapshotError, match="private verification"):
        candidate_snapshot.verify_workspace(root, encoded, digest, destination)

    assert not destination.exists()
    assert _candidate_stages(tmp_path) == []


def test_unsupported_atomic_publish_fails_closed_and_cleans_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, encoded, digest = _repo(tmp_path)
    destination = tmp_path / "destination"

    def unsupported(*_args: Any, **_kwargs: Any) -> None:
        raise candidate_snapshot.CandidateSnapshotError("UNSUPPORTED_ATOMIC_PUBLISH: injected")

    monkeypatch.setattr(candidate_snapshot, "_publish_private_directory_noreplace", unsupported)
    with pytest.raises(candidate_snapshot.CandidateSnapshotError, match="UNSUPPORTED_ATOMIC_PUBLISH"):
        candidate_snapshot.verify_workspace(root, encoded, digest, destination)

    assert not destination.exists()
    assert _candidate_stages(tmp_path) == []


def test_destination_created_after_preflight_is_never_overwritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, encoded, digest = _repo(tmp_path)
    destination = tmp_path / "destination"
    original_publish = candidate_snapshot._publish_private_directory_noreplace

    def race_publish(
        parent: Any, stage_name: str, stage_handle: int, destination_name: str,
    ) -> None:
        competitor = parent.path / destination_name
        competitor.mkdir()
        (competitor / "sentinel.bin").write_bytes(b"concurrent winner")
        original_publish(parent, stage_name, stage_handle, destination_name)

    monkeypatch.setattr(candidate_snapshot, "_publish_private_directory_noreplace", race_publish)
    with pytest.raises(candidate_snapshot.CandidateSnapshotError, match="destination appeared"):
        candidate_snapshot.verify_workspace(root, encoded, digest, destination)

    assert (destination / "sentinel.bin").read_bytes() == b"concurrent winner"
    assert len(list(destination.iterdir())) == 1
    assert _candidate_stages(tmp_path) == []


@pytest.mark.skipif(os.name != "nt", reason="requires native Windows relative-handle directory rename")
def test_windows_native_relative_handle_publish_authenticates_candidate(tmp_path: Path) -> None:
    """Native Windows integration coverage; not executed in the compatibility round."""
    root, encoded, digest = _repo(tmp_path)
    destination = tmp_path / "native-destination"

    snapshot = candidate_snapshot.verify_workspace(root, encoded, digest, destination)

    assert snapshot.materialized_root == destination.absolute()
    assert (destination / "src/app.py").read_bytes() == b"line one\r\nline two\r\n"
    assert candidate_snapshot.verify_materialized(destination, encoded, digest).source_tree == snapshot.source_tree
    assert _candidate_stages(tmp_path) == []


@pytest.mark.skipif(os.name != "nt", reason="requires native Windows destination checks")
@pytest.mark.parametrize("sentinel", [None, b"preexisting destination"])
def test_windows_native_existing_empty_or_nonempty_destination_is_preserved(
    tmp_path: Path, sentinel: bytes | None,
) -> None:
    """Native Windows integration coverage; preflight must reject both cases."""
    root, encoded, digest = _repo(tmp_path)
    destination = tmp_path / "existing-destination"
    destination.mkdir()
    if sentinel is not None:
        (destination / "sentinel.bin").write_bytes(sentinel)

    with pytest.raises(candidate_snapshot.CandidateSnapshotError, match="must not already exist"):
        candidate_snapshot.verify_workspace(root, encoded, digest, destination)

    assert destination.is_dir()
    assert sorted(path.name for path in destination.iterdir()) == ([] if sentinel is None else ["sentinel.bin"])
    if sentinel is not None:
        assert (destination / "sentinel.bin").read_bytes() == sentinel
    assert _candidate_stages(tmp_path) == []


@pytest.mark.skipif(os.name != "nt", reason="requires native Windows relative-handle no-replace rename")
def test_windows_native_concurrent_destination_is_never_overwritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Injected competitor timing, followed by the real native Windows rename call."""
    root, encoded, digest = _repo(tmp_path)
    destination = tmp_path / "native-race-destination"
    original_publish = candidate_snapshot._publish_private_directory_noreplace

    def create_competitor_then_publish(
        parent: Any, stage_name: str, stage_handle: int, destination_name: str,
    ) -> None:
        competitor = parent.path / destination_name
        competitor.mkdir()
        (competitor / "sentinel.bin").write_bytes(b"concurrent winner")
        original_publish(parent, stage_name, stage_handle, destination_name)

    monkeypatch.setattr(
        candidate_snapshot, "_publish_private_directory_noreplace", create_competitor_then_publish,
    )
    with pytest.raises(candidate_snapshot.CandidateSnapshotError, match="destination appeared"):
        candidate_snapshot.verify_workspace(root, encoded, digest, destination)

    assert (destination / "sentinel.bin").read_bytes() == b"concurrent winner"
    assert len(list(destination.iterdir())) == 1
    assert _candidate_stages(tmp_path) == []


@pytest.mark.skipif(os.name != "nt", reason="requires Windows materializer with an injected NTSTATUS")
def test_windows_native_operation_failure_fails_closed_and_cleans_own_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Simulated NTSTATUS failure; this does not exercise the native rename implementation."""
    root, encoded, digest = _repo(tmp_path)
    destination = tmp_path / "native-failure-destination"

    def native_not_supported(*_args: Any, **_kwargs: Any) -> int:
        return -1073741637  # STATUS_NOT_SUPPORTED (0xC00000BB)

    monkeypatch.setattr(candidate_snapshot, "_windows_ntdll_set_information_file", native_not_supported)
    with pytest.raises(candidate_snapshot.CandidateSnapshotError, match="UNSUPPORTED_ATOMIC_PUBLISH"):
        candidate_snapshot.verify_workspace(root, encoded, digest, destination)

    assert not destination.exists()
    assert _candidate_stages(tmp_path) == []


def test_two_thread_publishers_have_one_winner_without_overwrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Threaded race simulation; this is not a native filesystem stress test."""
    root, encoded, digest = _repo(tmp_path)
    destination = tmp_path / "shared-destination"
    barrier = Barrier(2)
    original_publish = candidate_snapshot._publish_private_directory_noreplace

    def synchronized_publish(
        parent: Any, stage_name: str, stage_handle: int, destination_name: str,
    ) -> None:
        barrier.wait(timeout=5)
        original_publish(parent, stage_name, stage_handle, destination_name)

    monkeypatch.setattr(candidate_snapshot, "_publish_private_directory_noreplace", synchronized_publish)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(candidate_snapshot.verify_workspace, root, encoded, digest, destination)
            for _ in range(2)
        ]
        outcomes: list[str] = []
        for future in futures:
            try:
                snapshot = future.result(timeout=12)
                assert snapshot.materialized_root == destination.absolute()
                outcomes.append("published")
            except candidate_snapshot.CandidateSnapshotError as exc:
                assert "destination appeared" in str(exc)
                outcomes.append("conflict")

    assert sorted(outcomes) == ["conflict", "published"]
    assert (destination / "src/app.py").read_bytes() == b"line one\r\nline two\r\n"
    assert _candidate_stages(tmp_path) == []


def test_orphaned_private_stage_is_never_treated_as_a_candidate(
    tmp_path: Path,
) -> None:
    root, encoded, digest = _repo(tmp_path)
    orphan = tmp_path / ".cand03-orphan"
    orphan.mkdir()
    (orphan / "src").mkdir()
    (orphan / "src/app.py").write_bytes(b"unverified orphan")
    destination = tmp_path / "new-destination"

    snapshot = candidate_snapshot.verify_workspace(root, encoded, digest, destination)

    assert snapshot.materialized_root == destination.absolute()
    assert (orphan / "src/app.py").read_bytes() == b"unverified orphan"
    assert (destination / "src/app.py").read_bytes() == b"line one\r\nline two\r\n"


def test_parent_identity_check_failure_cleans_private_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Injected identity-check failure; not a native Windows parent replacement test."""
    root, encoded, digest = _repo(tmp_path)
    destination = tmp_path / "destination"
    original_check = candidate_snapshot._ensure_parent_still_pinned
    calls = 0

    def reject_once(parent: Any) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise candidate_snapshot.CandidateSnapshotError("injected parent identity change")
        original_check(parent)

    monkeypatch.setattr(candidate_snapshot, "_ensure_parent_still_pinned", reject_once)
    with pytest.raises(candidate_snapshot.CandidateSnapshotError, match="parent identity change"):
        candidate_snapshot.verify_workspace(root, encoded, digest, destination)

    assert not destination.exists()
    assert _candidate_stages(tmp_path) == []


def test_cleanup_failure_is_reported_as_unconfirmed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, encoded, digest = _repo(tmp_path)
    destination = tmp_path / "destination"
    cleanup_name = "_cleanup_windows_stage" if os.name == "nt" else "_cleanup_linux_stage"

    def fail_cleanup(*_args: Any, **_kwargs: Any) -> None:
        raise OSError("injected cleanup failure")

    monkeypatch.setattr(candidate_snapshot, cleanup_name, fail_cleanup)

    def reject_private_tree(_root: Path, _manifest: dict[str, Any]) -> None:
        raise candidate_snapshot.CandidateSnapshotError("injected failure before cleanup")

    monkeypatch.setattr(candidate_snapshot, "_verify_private_tree", reject_private_tree)
    with pytest.raises(candidate_snapshot.CandidateSnapshotError, match="CLEANUP_UNCONFIRMED"):
        candidate_snapshot.verify_workspace(root, encoded, digest, destination)

    assert not destination.exists()
    assert len(_candidate_stages(tmp_path)) == 1

@pytest.mark.skipif(os.name != "nt", reason="requires native Windows junction support")
def test_windows_native_junction_parent_is_rejected(tmp_path: Path) -> None:
    """Native Windows integration coverage; not executed in the corrective round."""
    root, encoded, digest = _repo(tmp_path)
    junction_target = tmp_path / "junction-target"
    junction_target.mkdir()
    junction = tmp_path / "junction"
    try:
        subprocess.run(
            ["cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(junction_target)],
            check=True,
            capture_output=True,
            text=True,
        )
        junction_info = junction.lstat()
        assert getattr(junction_info, "st_reparse_tag", None) == 0xA0000003

        destination = junction / "destination"
        with pytest.raises(candidate_snapshot.CandidateSnapshotError, match="real directory"):
            candidate_snapshot.verify_workspace(root, encoded, digest, destination)

        assert not (junction_target / "destination").exists()
        assert _candidate_stages(junction_target) == []
    finally:
        if junction.exists() and getattr(junction.lstat(), "st_file_attributes", 0) & 0x400:
            junction.rmdir()

def test_workspace_hash_failure_leaves_no_destination_or_staging(
    tmp_path: Path,
) -> None:
    root, encoded, digest = _repo(tmp_path)
    (root / "src/app.py").write_bytes(b"tampered")
    destination = tmp_path / "destination"

    with pytest.raises(candidate_snapshot.CandidateSnapshotError, match="bytes"):
        candidate_snapshot.verify_workspace(root, encoded, digest, destination)

    assert not destination.exists()
    assert _candidate_stages(tmp_path) == []

def test_windows_pending_status_waits_for_final_io_status() -> None:
    io_status = type("IoStatus", (), {})()
    io_status.StatusUnion = type("StatusUnion", (), {"Status": 0x00000103})()
    waits: list[bool] = []

    def complete() -> int:
        waits.append(True)
        io_status.StatusUnion.Status = 0
        return 0

    result = candidate_snapshot._windows_final_io_status(
        0x00000103, io_status, complete, resources=(b"rename-buffer",),
    )

    assert result == 0
    assert waits == [True]


def test_windows_pending_status_with_nonconclusive_wait_fails_closed() -> None:
    io_status = type("IoStatus", (), {})()
    io_status.StatusUnion = type("StatusUnion", (), {"Status": 0x00000103})()
    retained = object()

    with pytest.raises(candidate_snapshot._WindowsPublicationIndeterminate, match="indeterminate") as caught:
        candidate_snapshot._windows_final_io_status(
            0x00000103, io_status, lambda: -1073741823, resources=(retained,),
        )

    assert caught.value.resources == (retained,)
    assert io_status.StatusUnion.Status == 0x00000103


def test_windows_pending_status_without_final_io_status_is_indeterminate() -> None:
    io_status = type("IoStatus", (), {})()
    io_status.StatusUnion = type("StatusUnion", (), {"Status": 0x00000103})()

    with pytest.raises(candidate_snapshot._WindowsPublicationIndeterminate, match="did not replace"):
        candidate_snapshot._windows_final_io_status(0x00000103, io_status, lambda: 0)


def test_windows_synchronous_status_must_match_io_status() -> None:
    io_status = type("IoStatus", (), {})()
    io_status.StatusUnion = type("StatusUnion", (), {"Status": 1})()

    with pytest.raises(candidate_snapshot._WindowsPublicationIndeterminate, match="disagrees"):
        candidate_snapshot._windows_final_io_status(0, io_status, lambda: pytest.fail("unexpected wait"))


@pytest.mark.skipif(os.name != "nt", reason="requires native Windows handle-based staging cleanup")
def test_windows_native_cleanup_pins_stage_name_and_deletes_by_handle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = candidate_snapshot._open_destination_parent(tmp_path / "new-destination")
    stage_handle: int | None = None
    stage_path: Path | None = None
    try:
        stage_name, stage_handle, identity, stage_path = candidate_snapshot._create_windows_private_stage(parent)
        assert stage_path is not None
        child_path = stage_path / "partial.bin"
        child_path.write_bytes(b"operation-owned partial stage")
        kernel, _ = candidate_snapshot._windows_api()
        child_probe = candidate_snapshot._windows_open_path(
            child_path, 0x00000080 | 0x00020000,
            share_mode=0x00000001 | 0x00000002, kernel=kernel,
        )
        try:
            child_identity, _ = candidate_snapshot._windows_file_identity_attributes(child_probe, kernel)
        finally:
            candidate_snapshot._windows_close_handle(kernel, child_probe)
        original_mark = candidate_snapshot._windows_mark_directory_for_deletion
        rename_blocked: list[bool] = []

        def race_final_name(kernel_arg: Any, handle_arg: int) -> None:
            file_identity, _ = candidate_snapshot._windows_file_identity_attributes(handle_arg, kernel_arg)
            if file_identity == child_identity:
                path_to_replace = child_path
                displaced = tmp_path / "displaced-child"
            elif file_identity == identity:
                path_to_replace = stage_path
                displaced = tmp_path / "displaced-stage"
            else:
                pytest.fail("cleanup attempted to delete an unexpected handle")
            try:
                path_to_replace.rename(displaced)
            except OSError:
                rename_blocked.append(True)
            else:
                rename_blocked.append(False)
            original_mark(kernel_arg, handle_arg)

        monkeypatch.setattr(candidate_snapshot, "_windows_mark_directory_for_deletion", race_final_name)
        candidate_snapshot._cleanup_windows_stage(parent, stage_name, identity, stage_handle)
        candidate_snapshot._close_stage_handle(stage_handle)
        stage_handle = None

        assert rename_blocked == [True, True]
        assert not stage_path.exists()
        assert not child_path.exists()
    finally:
        if stage_handle is not None:
            candidate_snapshot._close_stage_handle(stage_handle)
        candidate_snapshot._close_destination_parent(parent)

def test_windows_indeterminate_publication_retains_resources_and_skips_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = candidate_snapshot._PinnedDestinationParent(
        tmp_path, "destination", (1, 2), handle=99,
    )
    handle = 123
    retained = object()
    calls: list[str] = []
    monkeypatch.setattr(candidate_snapshot, "_ensure_destination_absent", lambda *_args: None)
    monkeypatch.setattr(
        candidate_snapshot, "_create_private_stage",
        lambda _parent: (".cand03-test", handle, (3, 4), tmp_path / ".cand03-test"),
    )
    monkeypatch.setattr(candidate_snapshot, "_build_private_stage_tree", lambda *_args: "tree")
    def raise_indeterminate(*_args: Any) -> None:
        raise candidate_snapshot._WindowsPublicationIndeterminate("indeterminate", (retained,))

    monkeypatch.setattr(candidate_snapshot, "_rename_verified_stage", raise_indeterminate)
    monkeypatch.setattr(
        candidate_snapshot, "_cleanup_unpublished_stage",
        lambda *_args: calls.append("cleanup"),
    )
    monkeypatch.setattr(
        candidate_snapshot, "_close_stage_handle",
        lambda *_args: calls.append("close-stage"),
    )
    monkeypatch.setattr(
        candidate_snapshot, "_close_destination_parent",
        lambda *_args: calls.append("close-parent"),
    )

    before = len(candidate_snapshot._WINDOWS_INDETERMINATE_PUBLICATIONS)
    with pytest.raises(candidate_snapshot._WindowsPublicationIndeterminate) as caught:
        candidate_snapshot._materialize_with_parent(
            tmp_path, {}, tmp_path / "destination", parent,
        )

    assert caught.value.resources == (retained,)
    assert len(candidate_snapshot._WINDOWS_INDETERMINATE_PUBLICATIONS) == before + 1
    assert calls == []




@pytest.mark.parametrize(
    ("reported_device", "reported_file_id", "actual", "expected"),
    [
        (0, 0, (10, 20), True),
        (10, 20, (10, 20), True),
        (10, 0, (10, 20), True),
        (0, 20, (10, 20), True),
        (11, 20, (10, 20), False),
        (10, 21, (10, 20), False),
    ],
)
def test_windows_cleanup_identity_checks_every_stat_component_available(
    reported_device: int, reported_file_id: int, actual: tuple[int, int], expected: bool,
) -> None:
    """Policy-only simulation for Windows stat identifiers, including unavailable zero fields."""
    assert candidate_snapshot._windows_cleanup_identity_is_consistent(
        actual, reported_device, reported_file_id,
    ) is expected


def test_windows_synchronous_return_status_is_final_when_io_block_is_untouched() -> None:
    io_status = type("IoStatus", (), {})()
    io_status.StatusUnion = type("StatusUnion", (), {"Status": 0x00000103})()
    result = candidate_snapshot._windows_final_io_status(
        0, io_status, lambda: pytest.fail("unexpected wait"), synchronous=True,
    )
    assert result == 0
    assert io_status.StatusUnion.Status == 0x00000103


def test_windows_unconfirmed_non_pending_status_fails_closed_without_sync_contract() -> None:
    io_status = type("IoStatus", (), {})()
    io_status.StatusUnion = type("StatusUnion", (), {"Status": 0x00000103})()
    with pytest.raises(candidate_snapshot._WindowsPublicationIndeterminate, match="did not replace"):
        candidate_snapshot._windows_final_io_status(
            0, io_status, lambda: pytest.fail("unexpected wait"),
        )

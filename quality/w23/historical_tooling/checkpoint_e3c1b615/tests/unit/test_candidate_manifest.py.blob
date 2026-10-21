"""Exact-byte candidate schema and resolver tests using disposable Git repos."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
from pathlib import Path
from typing import Any

import pytest

from distribution.candidate_manifest import (
    CandidateManifestError,
    canonical_manifest_bytes,
    parse_manifest,
    validate_manifest,
)
from distribution.candidate_snapshot import (
    CandidateSnapshotError,
    _is_reparse,
    _safe_file,
    verify_materialized,
    verify_workspace,
)


def _git(root: Path, *args: str, input_bytes: bytes | None = None, env: dict[str, str] | None = None) -> str:
    child_env = dict(os.environ if env is None else env)
    child_env.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull})
    result = subprocess.run(["git", *args], cwd=root, env=child_env, input=input_bytes,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if result.returncode:
        raise AssertionError(result.stderr.decode("utf-8", "replace"))
    return result.stdout.decode("ascii").strip()


def _file(path: str, content: bytes, mode: str = "100644") -> dict[str, Any]:
    return {"path": path, "mode": mode, "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}


def _repo(tmp_path: Path, *, payload: bytes = b"line one\r\nline two\r\n") -> tuple[Path, bytes, str]:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "candidate@example.invalid")
    _git(root, "config", "user.name", "Candidate Test")
    source_files = {
        "src/app.py": payload,
        "tests/test_app.py": b"assert True\n",
        "docs/guide.md": b"guide\n",
        "tools/check.py": b"print('ok')\n",
    }
    for name, content in source_files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    _git(root, "add", "--all")
    _git(root, "commit", "-qm", "base")
    head = _git(root, "rev-parse", "HEAD")
    tree = _git(root, "rev-parse", "HEAD^{tree}")
    for name, content in {
        "authority.json": b"authority\n",
        "inventory.json": b"inventory\n",
    }.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    manifest = {
        "schema_version": 1,
        "kind": "EXACT_BYTE_REPOSITORY_CANDIDATE",
        "unit": "W23_FINAL_CANDIDATE",
        "object_format": _git(root, "rev-parse", "--show-object-format"),
        "base": {"head": head, "tree": tree},
        "source_tree": tree,
        "files": sorted((_file("docs/guide.md", b"guide\n"), _file("src/app.py", payload),
                         _file("tests/test_app.py", b"assert True\n"), _file("tools/check.py", b"print('ok')\n")),
                        key=lambda item: item["path"]),
        "overlay": [],
        "lineage": {
            "authority_predecessor": {"path": "authority.json", "sha256": hashlib.sha256(b"authority\n").hexdigest()},
            "inherited_observation": {"path": "inventory.json", "sha256": hashlib.sha256(b"inventory\n").hexdigest()},
            "acceptance_records": [],
        },
        "selected_evidence": [],
        "workspace_context": {
            "protected_files": [_file("authority.json", b"authority\n"), _file("inventory.json", b"inventory\n")],
            "operational_roots": [],
        },
    }
    encoded = canonical_manifest_bytes(manifest)
    selection = tmp_path / "candidate.json"
    selection.write_bytes(encoded)
    return root, encoded, hashlib.sha256(encoded).hexdigest()


def _overlay_candidate(
    tmp_path: Path, changes: dict[str, bytes | None], *, unit: str = "W23_FINAL_CANDIDATE",
) -> tuple[Path, bytes, str, dict[str, bytes]]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    root, encoded, _ = _repo(tmp_path)
    manifest = json.loads(encoded)
    base_entries: dict[str, dict[str, str]] = {}
    raw_tree = subprocess.run(["git", "ls-tree", "-r", "-z", "--full-tree", manifest["base"]["tree"]],
                              cwd=root, check=True, stdout=subprocess.PIPE).stdout
    for record in raw_tree.split(b"\0"):
        if record:
            header, path = record.split(b"\t", 1)
            mode, _kind, oid = header.decode("ascii").split()
            base_entries[path.decode("utf-8")] = {"mode": mode, "blob": oid}
    content = {row["path"]: (root / row["path"]).read_bytes() for row in manifest["files"]}
    for path, value in changes.items():
        if value is None:
            content.pop(path, None)
            (root / path).unlink(missing_ok=True)
        else:
            content[path] = value
            target = root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(value)
    tree_root = tmp_path / "expected-tree"
    tree_root.mkdir()
    _git(tree_root, "init", "-q")
    _git(tree_root, "config", "user.email", "candidate@example.invalid")
    _git(tree_root, "config", "user.name", "Candidate Test")
    _git(tree_root, "config", "core.autocrlf", "false")
    for path, value in content.items():
        target = tree_root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(value)
    _git(tree_root, "add", "--all")
    manifest["source_tree"] = _git(tree_root, "write-tree")
    manifest["unit"] = unit
    manifest["files"] = sorted((_file(path, value) for path, value in content.items()), key=lambda row: row["path"])
    manifest["overlay"] = []
    for path, value in changes.items():
        before = base_entries.get(path)
        operation = "add" if before is None else "delete" if value is None else "modify"
        manifest["overlay"].append({
            "path": path, "operation": operation, "before": before,
            "after_sha256": None if value is None else hashlib.sha256(value).hexdigest(),
            "acceptance_record_id": "synthetic-decision",
        })
    manifest["overlay"].sort(key=lambda row: row["path"])
    manifest["lineage"]["acceptance_records"] = [{
        "id": "synthetic-decision",
        "decision": manifest["lineage"]["authority_predecessor"],
        "evidence": [],
    }]
    encoded = canonical_manifest_bytes(manifest)
    (tmp_path / "candidate.json").write_bytes(encoded)
    return root, encoded, hashlib.sha256(encoded).hexdigest(), content


def test_workspace_candidate_reconstructs_and_materializes_exact_raw_bytes(tmp_path: Path) -> None:
    root, encoded, digest = _repo(tmp_path)
    index_digest = hashlib.sha256((root / ".git/index").read_bytes()).hexdigest()
    snapshot = verify_workspace(root, encoded, digest, tmp_path / "materialized")
    assert snapshot.source_tree == snapshot.manifest["source_tree"]
    assert (snapshot.materialized_root / "src/app.py").read_bytes() == b"line one\r\nline two\r\n"
    assert hashlib.sha256((root / ".git/index").read_bytes()).hexdigest() == index_digest
    independent = verify_materialized(snapshot.materialized_root, encoded, digest)
    assert independent.source_tree == snapshot.source_tree


@pytest.mark.parametrize("extra", ["authority.json", ".git/config", "cache/unexpected.bin"])
def test_materialized_tree_rejects_workspace_only_and_admin_content(tmp_path: Path, extra: str) -> None:
    root, encoded, digest = _repo(tmp_path)
    materialized = verify_workspace(root, encoded, digest, tmp_path / "materialized").materialized_root
    extra_path = materialized.joinpath(*extra.split("/"))
    extra_path.parent.mkdir(parents=True, exist_ok=True)
    extra_path.write_bytes(b"unexpected")
    with pytest.raises(CandidateSnapshotError):
        verify_materialized(materialized, encoded, digest)


def test_workspace_operational_root_is_excluded_but_not_materialized(tmp_path: Path) -> None:
    root, encoded, _ = _repo(tmp_path)
    cache = root / "cache"
    cache.mkdir()
    (cache / "runtime.bin").write_bytes(b"ephemeral")
    document = json.loads(encoded)
    document["workspace_context"]["operational_roots"] = ["cache"]
    selected = canonical_manifest_bytes(document)
    digest = hashlib.sha256(selected).hexdigest()
    snapshot = verify_workspace(root, selected, digest, tmp_path / "materialized")
    assert not (snapshot.materialized_root / "cache").exists()


@pytest.mark.parametrize(("path", "content", "operation"), [
    ("src/new.py", b"added\r\n", "add"),
    ("src/app.py", b"modified\r\n", "modify"),
    ("tools/check.py", None, "delete"),
    ("docs/guide.md", b"docs changed\n", "modify"),
    ("tests/test_app.py", b"assert 2 == 2\n", "modify"),
])
def test_add_modify_delete_and_scoped_changes_reconstruct_exact_tree(
    tmp_path: Path, path: str, content: bytes | None, operation: str,
) -> None:
    root, encoded, digest, files = _overlay_candidate(tmp_path, {path: content})
    manifest = parse_manifest(encoded, digest)
    assert manifest["overlay"][0]["operation"] == operation
    snapshot = verify_workspace(root, encoded, digest, tmp_path / "materialized")
    assert snapshot.source_tree == manifest["source_tree"]
    for name, data in files.items():
        assert snapshot.materialized_root.joinpath(*name.split("/")).read_bytes() == data
    assert not (snapshot.materialized_root / "tools/check.py").exists() if path == "tools/check.py" else True


def test_generic_schema_accepts_a_future_wave_unit_and_repeatable_tree(tmp_path: Path) -> None:
    root, encoded, digest, _ = _overlay_candidate(tmp_path, {"src/app.py": b"future\n"}, unit="W99_EXPERIMENT")
    first = verify_workspace(root, encoded, digest, tmp_path / "first")
    second = verify_workspace(root, encoded, digest, tmp_path / "second")
    assert first.manifest["unit"] == "W99_EXPERIMENT"
    assert first.source_tree == second.source_tree


def test_crlf_and_lf_have_distinct_digest_manifest_and_tree(tmp_path: Path) -> None:
    (tmp_path / "crlf").mkdir()
    (tmp_path / "lf").mkdir()
    crlf_root, crlf_bytes, crlf_digest = _repo(tmp_path / "crlf")
    lf_root, lf_bytes, lf_digest = _repo(tmp_path / "lf", payload=b"line one\nline two\n")
    assert hashlib.sha256(b"line one\r\nline two\r\n").digest() != hashlib.sha256(b"line one\nline two\n").digest()
    assert crlf_digest != lf_digest
    assert verify_workspace(crlf_root, crlf_bytes, crlf_digest, tmp_path / "crlf-out").source_tree != verify_workspace(
        lf_root, lf_bytes, lf_digest, tmp_path / "lf-out").source_tree


@pytest.mark.parametrize("path", [
    "../escape", "/absolute", "C:/drive", "//server/share", "a\\b", "file:stream",
    "CON.txt", "aux", "a//b", "a/./b", "a/../b", "bad\x00name",
])
def test_unsafe_repository_paths_are_rejected(path: str) -> None:
    from distribution.candidate_manifest import validate_repo_path
    with pytest.raises(CandidateManifestError):
        validate_repo_path(path)


def test_closed_schema_digest_and_duplicate_key_checks(tmp_path: Path) -> None:
    _, encoded, digest = _repo(tmp_path)
    parsed = parse_manifest(encoded, digest)
    parsed["future_field"] = True
    with pytest.raises(CandidateManifestError):
        validate_manifest(parsed)
    with pytest.raises(CandidateManifestError, match="digest"):
        parse_manifest(encoded, "0" * 64)
    duplicate = encoded.replace(b'"kind":"EXACT_BYTE_REPOSITORY_CANDIDATE"',
                                b'"kind":"EXACT_BYTE_REPOSITORY_CANDIDATE","kind":"other"')
    with pytest.raises(CandidateManifestError, match="duplicate"):
        parse_manifest(duplicate, hashlib.sha256(duplicate).hexdigest())


def test_unexpected_ignored_file_and_staged_divergence_are_rejected(tmp_path: Path) -> None:
    root, encoded, digest = _repo(tmp_path)
    unexpected = root / "ignored.pyc"
    unexpected.write_bytes(b"unexpected")
    (root / ".gitignore").write_text("ignored.pyc\n", encoding="ascii")
    with pytest.raises(CandidateSnapshotError, match="path set"):
        verify_workspace(root, encoded, digest, tmp_path / "out-ignored")
    unexpected.unlink()
    (root / ".gitignore").unlink()
    (root / "src/app.py").write_bytes(b"staged")
    _git(root, "add", "src/app.py")
    with pytest.raises(CandidateSnapshotError):
        verify_workspace(root, encoded, digest, tmp_path / "out-staged")


@pytest.mark.parametrize("relative", ["src/app.py", "tests/test_app.py", "tools/check.py", "docs/guide.md"])
def test_changed_production_test_tool_and_document_bytes_are_rejected(
    tmp_path: Path, relative: str,
) -> None:
    root, encoded, digest = _repo(tmp_path)
    target = root / relative
    target.write_bytes(target.read_bytes() + b"tampered")
    with pytest.raises(CandidateSnapshotError, match="bytes"):
        verify_workspace(root, encoded, digest, tmp_path / "out")


@pytest.mark.parametrize("field", ["sha256", "bytes"])
def test_manifest_byte_hash_and_length_mismatches_are_rejected(tmp_path: Path, field: str) -> None:
    replacement = b"replacement content"
    root, encoded, digest, _ = _overlay_candidate(tmp_path, {"src/app.py": replacement})
    tampered = b"x" * (len(replacement) + (1 if field == "bytes" else 0))
    (root / "src/app.py").write_bytes(tampered)
    with pytest.raises(CandidateSnapshotError, match="bytes"):
        verify_workspace(root, encoded, digest, tmp_path / "out")


@pytest.mark.parametrize("base_field", ["head", "tree"])
def test_stale_pinned_base_commit_or_tree_is_rejected(tmp_path: Path, base_field: str) -> None:
    root, encoded, _ = _repo(tmp_path)
    document = json.loads(encoded)
    document["base"][base_field] = "0" * len(document["base"][base_field])
    invalid = canonical_manifest_bytes(document)
    with pytest.raises(CandidateSnapshotError, match="HEAD/tree"):
        verify_workspace(root, invalid, hashlib.sha256(invalid).hexdigest(), tmp_path / "out")


def test_wrong_external_digest_and_crlf_normalization_are_rejected(tmp_path: Path) -> None:
    root, encoded, digest = _repo(tmp_path)
    with pytest.raises(CandidateSnapshotError, match="digest"):
        verify_workspace(root, encoded, "0" * 64, tmp_path / "out-digest")
    (root / "src/app.py").write_bytes(b"line one\nline two\n")
    with pytest.raises(CandidateSnapshotError, match="bytes"):
        verify_workspace(root, encoded, digest, tmp_path / "out-lf")


@pytest.mark.parametrize("case", [
    "duplicate_path", "case_collision", "unsupported_mode", "malformed_sha", "malformed_oid",
    "unsorted_files", "source_protected_overlap", "source_operational_overlap",
])
def test_manifest_collection_and_identity_invariants(case: str, tmp_path: Path) -> None:
    _, encoded, _ = _repo(tmp_path)
    document = json.loads(encoded)
    if case == "duplicate_path":
        document["files"].append(dict(document["files"][0]))
    elif case == "case_collision":
        duplicate = dict(document["files"][0])
        duplicate["path"] = duplicate["path"].upper()
        document["files"].append(duplicate)
    elif case == "unsupported_mode":
        document["files"][0]["mode"] = "120000"
    elif case == "malformed_sha":
        document["files"][0]["sha256"] = "G" * 64
    elif case == "malformed_oid":
        document["base"]["head"] = "z" * 40
    elif case == "unsorted_files":
        document["files"].reverse()
    elif case == "source_protected_overlap":
        protected = document["workspace_context"]["protected_files"][0]
        document["files"].append(_file(protected["path"], b"authority\n"))
        document["files"].sort(key=lambda row: row["path"])
    else:
        document["workspace_context"]["operational_roots"] = [document["files"][0]["path"]]
    with pytest.raises(CandidateManifestError):
        validate_manifest(document)


def test_overlay_path_substitution_and_omitted_deletion_are_rejected(tmp_path: Path) -> None:
    root, encoded, digest, _ = _overlay_candidate(
        tmp_path / "substitution", {"tools/check.py": None, "tools/replacement.py": b"print('ok')\n"}
    )
    verify_workspace(root, encoded, digest, tmp_path / "substitution-out")

    root2, encoded2, _, _ = _overlay_candidate(tmp_path / "omitted", {"tools/check.py": None})
    document = json.loads(encoded2)
    document["overlay"] = []
    invalid = canonical_manifest_bytes(document)
    with pytest.raises(CandidateSnapshotError, match="overlay"):
        verify_workspace(root2, invalid, hashlib.sha256(invalid).hexdigest(), tmp_path / "omitted-out")


def test_wrong_file_bytes_and_missing_source_fail_closed(tmp_path: Path) -> None:
    root, encoded, digest = _repo(tmp_path)
    (root / "tests/test_app.py").write_bytes(b"changed")
    with pytest.raises(CandidateSnapshotError, match="bytes"):
        verify_workspace(root, encoded, digest, tmp_path / "out-changed")
    (root / "tests/test_app.py").unlink()
    with pytest.raises((CandidateSnapshotError, FileNotFoundError)):
        verify_workspace(root, encoded, digest, tmp_path / "out-missing")


def test_unexpected_same_content_path_substitution_is_rejected(tmp_path: Path) -> None:
    root, encoded, digest = _repo(tmp_path)
    old = root / "tools/check.py"
    replacement = root / "tools/replacement.py"
    replacement.write_bytes(old.read_bytes())
    old.unlink()
    with pytest.raises(CandidateSnapshotError, match="path set"):
        verify_workspace(root, encoded, digest, tmp_path / "out-substitution")


def test_directory_cannot_satisfy_a_candidate_file_entry(tmp_path: Path) -> None:
    root, _, _ = _repo(tmp_path)
    with pytest.raises(CandidateSnapshotError, match="regular file"):
        _safe_file(root, "src")


def test_symlink_metadata_is_rejected_without_os_specific_link_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, encoded, digest = _repo(tmp_path)
    metadata = os.stat_result((stat.S_IFLNK | 0o777, 0, 0, 1, 0, 0, 0, 0, 0, 0))
    monkeypatch.setattr(Path, "lstat", lambda _path: metadata)
    with pytest.raises(CandidateSnapshotError, match="symlink"):
        _safe_file(root, "link.py")


def test_reparse_metadata_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, _, _ = _repo(tmp_path)

    class ReparseInfo:
        st_mode = stat.S_IFREG | 0o644
        st_file_attributes = 0x400

    monkeypatch.setattr(Path, "lstat", lambda _path: ReparseInfo())
    assert _is_reparse(ReparseInfo())
    with pytest.raises(CandidateSnapshotError, match="reparse"):
        _safe_file(root, "junction.py")


def test_source_tree_mismatch_fails_closed(tmp_path: Path) -> None:
    root, encoded, _ = _repo(tmp_path)
    document = json.loads(encoded)
    document["source_tree"] = "0" * len(document["source_tree"])
    altered = canonical_manifest_bytes(document)
    with pytest.raises(CandidateSnapshotError, match="source_tree"):
        verify_workspace(root, altered, hashlib.sha256(altered).hexdigest(), tmp_path / "out-tree")

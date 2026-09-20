"""Focused tests for the isolated complete-candidate Git tree."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

import pytest

from distribution.provenance import ProvenanceError, isolated_candidate_tree, materialize_candidate_tree


def _fixture_git(repository: Path, *arguments: str) -> str:
    return subprocess.check_output(["git", *arguments], cwd=repository, text=True).strip()


def _fixture_repository(
    tmp_path: Path,
    relative: str,
    *,
    additional_files: tuple[str, ...] = (),
) -> Path:
    repository = tmp_path / "candidate-repository"
    repository.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repository, check=True)
    subprocess.run(["git", "config", "core.autocrlf", "false"], cwd=repository, check=True)
    subprocess.run(["git", "config", "core.eol", "lf"], cwd=repository, check=True)
    subprocess.run(
        ["git", "config", "user.name", "W18 provenance test"],
        cwd=repository,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "w18-provenance@example.invalid"],
        cwd=repository,
        check=True,
    )
    source = repository / relative
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"before\n")
    (repository / "src.py").write_bytes(b"VALUE = 1\n")
    for additional in additional_files:
        path = repository / additional
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fixture\n")
    subprocess.run(["git", "add", "--all"], cwd=repository, check=True)
    subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "commit", "-qm", "fixture"],
        cwd=repository,
        check=True,
    )
    return repository


def test_candidate_tree_materialization_is_complete_and_read_only(tmp_path: Path) -> None:
    repository = _fixture_repository(
        tmp_path,
        "distribution/provenance.py",
        additional_files=("installer/install.ps1", "docs/wave18_phase0_inventory.json"),
    )
    (repository / "distribution" / "provenance.py").write_bytes(b"after\n")
    before = (
        _fixture_git(repository, "rev-parse", "HEAD"),
        _fixture_git(repository, "branch", "--show-current"),
        _fixture_git(repository, "diff", "--cached", "--name-only"),
        _fixture_git(repository, "status", "--short", "--untracked-files=all"),
    )

    with isolated_candidate_tree(repository) as snapshot:
        assert snapshot.base_commit == before[0]
        assert len(snapshot.tree) == 40
        assert snapshot.changed_paths == ("distribution/provenance.py",)
        with tempfile.TemporaryDirectory(prefix="wave18-test-materialized-") as directory:
            materialized = Path(directory)
            materialize_candidate_tree(snapshot, materialized)
            assert (materialized / "distribution" / "provenance.py").is_file()
            assert (materialized / "installer" / "install.ps1").is_file()
            assert (materialized / "docs" / "wave18_phase0_inventory.json").is_file()
            assert (materialized / "src.py").is_file()
            assert (materialized / "distribution" / "provenance.py").read_bytes() == b"after\n"
            hidden_directories = {
                child.name for child in materialized.iterdir() if child.is_dir() and child.name.startswith(".")
            }
            assert hidden_directories <= {".github"}

    assert before == (
        _fixture_git(repository, "rev-parse", "HEAD"),
        _fixture_git(repository, "branch", "--show-current"),
        _fixture_git(repository, "diff", "--cached", "--name-only"),
        _fixture_git(repository, "status", "--short", "--untracked-files=all"),
    )


def test_non_python_candidate_change_changes_the_real_candidate_tree(tmp_path: Path) -> None:
    repository = _fixture_repository(tmp_path, "README.md")
    baseline_tree = _fixture_git(repository, "rev-parse", "HEAD^{tree}")
    (repository / "README.md").write_bytes(b"after\n")

    with isolated_candidate_tree(repository) as snapshot:
        assert snapshot.base_tree == baseline_tree
        assert snapshot.tree != baseline_tree
        assert snapshot.changed_paths == ("README.md",)


def test_candidate_deletion_changes_the_real_candidate_tree(tmp_path: Path) -> None:
    repository = _fixture_repository(tmp_path, "installer/fixture.ps1")
    baseline_tree = _fixture_git(repository, "rev-parse", "HEAD^{tree}")
    (repository / "installer" / "fixture.ps1").unlink()

    with isolated_candidate_tree(repository) as snapshot:
        assert snapshot.base_tree == baseline_tree
        assert snapshot.tree != baseline_tree
        assert snapshot.changed_paths == ("installer/fixture.ps1",)


def test_candidate_tree_isolated_operation_preserves_index_objects_and_worktree(
    tmp_path: Path,
) -> None:
    repository = _fixture_repository(tmp_path, "README.md")
    (repository / "README.md").write_bytes(b"after\n")
    index = Path(_fixture_git(repository, "rev-parse", "--git-path", "index"))
    if not index.is_absolute():
        index = repository / index
    objects = Path(_fixture_git(repository, "rev-parse", "--git-path", "objects"))
    if not objects.is_absolute():
        objects = repository / objects
    index_before = index.read_bytes()
    objects_before = {
        path.relative_to(objects).as_posix()
        for path in objects.rglob("*")
        if path.is_file()
    }
    worktree_before = (repository / "README.md").read_bytes()

    with isolated_candidate_tree(repository) as snapshot:
        assert snapshot.tree != snapshot.base_tree

    assert index.read_bytes() == index_before
    assert {
        path.relative_to(objects).as_posix()
        for path in objects.rglob("*")
        if path.is_file()
    } == objects_before
    assert (repository / "README.md").read_bytes() == worktree_before


def test_w18_default_surface_rejects_a_non_w18_path(tmp_path: Path) -> None:
    repository = _fixture_repository(tmp_path, "agent/feature.py")
    (repository / "agent" / "feature.py").write_bytes(b"after\n")

    with pytest.raises(ProvenanceError, match="unexplained candidate path"):
        with isolated_candidate_tree(repository):
            pass


def test_explicit_candidate_surface_allows_prefixes_but_stays_bounded(tmp_path: Path) -> None:
    repository = _fixture_repository(tmp_path, "agent/feature.py")
    (repository / "agent" / "feature.py").write_bytes(b"after\n")

    with isolated_candidate_tree(repository, allowed_paths=("agent/",)) as snapshot:
        assert snapshot.changed_paths == ("agent/feature.py",)

    (repository / "src.py").write_bytes(b"after\n")
    with pytest.raises(ProvenanceError, match="unexplained candidate path"):
        with isolated_candidate_tree(repository, allowed_paths=("agent/",)):
            pass

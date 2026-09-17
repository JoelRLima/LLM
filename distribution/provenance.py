"""Read-only candidate provenance using an isolated Git index and object store."""

from __future__ import annotations

import inspect
import os
import re
import subprocess
import tarfile
import tempfile
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable, Iterator, Mapping, Sequence

_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_W18_ALLOWED_EXACT = {
    ".github/workflows/wave18-installed-product.yml",
    "README.md",
    "agent/__init__.py",
    "agent/_version.py",
    "agent/runtime/candidate_lease.py",
    "docs/operacao-standalone.md",
    "docs/wave18_phase0_inventory.json",
    "docs/wave18_phase0_seam_proof.md",
    "pyproject.toml",
    "quality/baseline.json",
    "scripts/build_release_artifacts.py",
    "scripts/build_windows_payload.py",
    "scripts/check_reproducible_release.py",
    "scripts/check_wave18_architecture.py",
    "scripts/build_conpty_authority_evidence.py",
    "scripts/conpty_authority.py",
    "scripts/probe_wave18_child_process.py",
    "scripts/probe_wave18_install_root_cleanup.py",
    "scripts/run_wave18_adversarial.py",
    "scripts/uv_build_evidence.py",
    "scripts/verify_installed_package.py",
    "scripts/verify_installed_product.py",
    "scripts/installer_fault_instrumentation.py",
    "scripts/w18_conpty.py",
}
_W18_ALLOWED_PREFIXES = ("distribution/", "installer/", "tests/")


class ProvenanceError(ValueError):
    """Raised when the candidate cannot be isolated and represented safely."""


@dataclass(frozen=True)
class CandidateTree:
    """A real Git tree backed by temporary objects for the lifetime of the context."""

    root: Path
    base_commit: str
    base_tree: str
    tree: str
    changed_paths: tuple[str, ...]
    git_environment: Mapping[str, str]
    temporary_root: Path


def assert_commit_tree_matches(audited_tree: str, commit_tree: str) -> None:
    """Fail closed unless a real post-audit commit points at the audited tree."""

    _validate_hex40(audited_tree, "audited candidate tree")
    _validate_hex40(commit_tree, "committed release tree")
    if audited_tree != commit_tree:
        raise ProvenanceError(
            "committed release tree differs from the Sol-audited candidate tree: "
            f"{commit_tree} != {audited_tree}"
        )


def seal_manifest_for_commit(
    document: Mapping[str, object],
    *,
    commit: str,
    commit_tree: str,
    audited_tree: str,
) -> dict[str, object]:
    """Seal only commit-bound provenance after tree equality is proven.

    The candidate id is checked before and after the transition.  This helper
    deliberately returns manifest metadata only; it never rebuilds the wheel,
    locks, installer payload, or any other audited bytes.
    """

    assert_commit_tree_matches(audited_tree, commit_tree)
    _validate_hex40(commit, "source.commit")
    source = document.get("source")
    if not isinstance(source, Mapping):
        raise ProvenanceError("manifest source is missing")
    if source.get("status") != "uncommitted_candidate" or source.get("commit") is not None:
        raise ProvenanceError("only an uncommitted candidate can be sealed")
    if source.get("tree") != audited_tree:
        raise ProvenanceError("manifest source.tree does not equal the audited candidate tree")
    original_candidate_id = document.get("candidate_id")
    sealed = deepcopy(dict(document))
    sealed_source = sealed.get("source")
    if not isinstance(sealed_source, dict):
        raise ProvenanceError("manifest source is not mutable")
    sealed_source["status"] = "committed_release"
    sealed_source["commit"] = commit
    if sealed.get("candidate_id") != original_candidate_id:
        raise ProvenanceError("candidate_id changed while sealing provenance")
    from .release_manifest import candidate_id, validate_manifest

    if candidate_id(sealed) != original_candidate_id:
        raise ProvenanceError("candidate_id is not invariant across provenance sealing")
    validate_manifest(sealed)
    return sealed


def _clean_repository_environment() -> dict[str, str]:
    environment = os.environ.copy()
    for variable in ("GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES"):
        environment.pop(variable, None)
    return environment


def _run_git(root: Path, arguments: Sequence[str], environment: Mapping[str, str] | None = None) -> str:
    try:
        completed = subprocess.run(
            ["git", *arguments],
            cwd=root,
            env=dict(environment) if environment is not None else _clean_repository_environment(),
            check=True,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "stderr", "") or getattr(exc, "stdout", "") or str(exc)
        raise ProvenanceError(f"git command failed: git {' '.join(arguments)}\n{detail[-2000:]}") from exc
    return completed.stdout.strip()


def _run_git_bytes(root: Path, arguments: Sequence[str]) -> bytes:
    try:
        completed = subprocess.run(
            ["git", *arguments],
            cwd=root,
            env=_clean_repository_environment(),
            check=True,
            capture_output=True,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "stderr", b"") or getattr(exc, "stdout", b"") or str(exc).encode()
        raise ProvenanceError(f"git command failed: git {' '.join(arguments)}\n{detail[-2000:]!r}") from exc
    return completed.stdout


def _normalise_path(value: str) -> str:
    path = value.replace("\\", "/")
    if not path or path.startswith("/") or "\x00" in path:
        raise ProvenanceError(f"invalid candidate path: {value!r}")
    if any(part in {"", ".", ".."} for part in path.split("/")):
        raise ProvenanceError(f"unsafe candidate path: {value!r}")
    return path


def _changed_paths(root: Path) -> tuple[str, ...]:
    staged = _run_git_bytes(root, ["diff", "--cached", "--name-only", "-z", "--"])
    if staged:
        names = [item.decode("utf-8") for item in staged.split(b"\x00") if item]
        raise ProvenanceError(f"real staging is not empty: {names}")

    tracked = _run_git_bytes(root, ["diff", "--name-only", "--no-renames", "-z", "HEAD", "--"])
    untracked = _run_git_bytes(root, ["ls-files", "--others", "--exclude-standard", "-z"])
    names = {
        _normalise_path(item.decode("utf-8"))
        for item in (*tracked.split(b"\x00"), *untracked.split(b"\x00"))
        if item
    }
    return tuple(sorted(names))


def _is_allowed_w18_path(path: str) -> bool:
    return path in _W18_ALLOWED_EXACT or any(path.startswith(prefix) for prefix in _W18_ALLOWED_PREFIXES)


def inventory_candidate_paths(root: Path, allowed_paths: Iterable[str] | None = None) -> tuple[str, ...]:
    """Inventory non-ignored worktree changes and reject unexplained paths."""

    root = root.resolve()
    changed = _changed_paths(root)
    if allowed_paths is None:
        unexplained = [path for path in changed if not _is_allowed_w18_path(path)]
    else:
        allowed = {_normalise_path(path) for path in allowed_paths}
        unexplained = [path for path in changed if path not in allowed]
    if unexplained:
        raise ProvenanceError(f"unexplained candidate path(s): {unexplained}")
    return changed


def _validate_hex40(value: str, label: str) -> str:
    if not _HEX40.fullmatch(value):
        raise ProvenanceError(f"{label} is not a 40-character Git object id")
    return value


@contextmanager
def isolated_candidate_tree(
    root: Path,
    *,
    allowed_paths: Iterable[str] | None = None,
) -> Iterator[CandidateTree]:
    """Yield a complete candidate tree without touching the real index or refs.

    The temporary index starts at ``HEAD`` and receives exactly the inventoried
    worktree delta. New blobs and tree objects are written to a temporary object
    database whose only alternate is the repository's existing object database.
    """

    root = root.resolve()
    if not root.is_dir():
        raise ProvenanceError(f"repository root does not exist: {root}")
    changed_paths = inventory_candidate_paths(root, allowed_paths)
    base_commit = _validate_hex40(_run_git(root, ["rev-parse", "HEAD"]), "HEAD")
    base_tree = _validate_hex40(
        _run_git(root, ["rev-parse", "HEAD^{tree}"]),
        "HEAD tree",
    )
    repository_objects = Path(_run_git(root, ["rev-parse", "--git-path", "objects"]))
    if not repository_objects.is_absolute():
        repository_objects = root / repository_objects
    repository_objects = repository_objects.resolve()
    if not repository_objects.is_dir():
        raise ProvenanceError(f"repository object database does not exist: {repository_objects}")

    with tempfile.TemporaryDirectory(prefix="wave18-candidate-git-") as temporary:
        temporary_root = Path(temporary)
        temporary_objects = temporary_root / "objects"
        temporary_objects.mkdir()
        temporary_index = temporary_root / "index"
        environment = _clean_repository_environment()
        environment["GIT_INDEX_FILE"] = str(temporary_index)
        environment["GIT_OBJECT_DIRECTORY"] = str(temporary_objects)
        environment["GIT_ALTERNATE_OBJECT_DIRECTORIES"] = str(repository_objects)

        _run_git(root, ["read-tree", base_commit], environment)
        if changed_paths:
            _run_git(root, ["add", "--all", "--", *changed_paths], environment)
        tree = _validate_hex40(_run_git(root, ["write-tree"], environment), "candidate tree")
        object_type = _run_git(root, ["cat-file", "-t", tree], environment)
        if object_type != "tree":
            raise ProvenanceError(f"candidate object {tree} is not a Git tree")
        yield CandidateTree(
            root=root,
            base_commit=base_commit,
            base_tree=base_tree,
            tree=tree,
            changed_paths=changed_paths,
            git_environment=environment,
            temporary_root=temporary_root,
        )


def materialize_candidate_tree(snapshot: CandidateTree, destination: Path) -> None:
    """Materialize exactly ``snapshot.tree`` into an isolated source directory."""

    destination = destination.resolve()
    if destination.exists():
        if not destination.is_dir() or any(destination.iterdir()):
            raise ProvenanceError(f"materialization destination is not an empty directory: {destination}")
    destination.mkdir(parents=True, exist_ok=True)
    archive_path = snapshot.temporary_root / "candidate.tar"
    _run_git(
        snapshot.root,
        ["archive", "--format=tar", "--output", str(archive_path), snapshot.tree],
        snapshot.git_environment,
    )
    try:
        with tarfile.open(archive_path, mode="r:") as archive:
            members = archive.getmembers()
            _validate_archive_members(members)
            _extract_archive(archive, destination, members)
    except (OSError, tarfile.TarError) as exc:
        raise ProvenanceError(f"cannot materialize candidate tree: {destination}") from exc


def _validate_archive_members(members: Sequence[tarfile.TarInfo]) -> None:
    for member in members:
        member_path = PurePosixPath(member.name)
        if member_path.is_absolute() or ".." in member_path.parts:
            raise ProvenanceError(f"unsafe Git tree archive member: {member.name}")


def _extract_archive(
    archive: tarfile.TarFile,
    destination: Path,
    members: Sequence[tarfile.TarInfo],
) -> None:
    if "filter" in inspect.signature(archive.extractall).parameters:
        archive.extractall(destination, members=members, filter="data")
        return
    archive.extractall(destination, members=members)


__all__ = [
    "assert_commit_tree_matches",
    "CandidateTree",
    "ProvenanceError",
    "inventory_candidate_paths",
    "isolated_candidate_tree",
    "materialize_candidate_tree",
    "seal_manifest_for_commit",
]

"""Verify and materialize exact-byte repository candidates in isolated Git state."""

from __future__ import annotations

import hashlib
import os
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from distribution.candidate_manifest import (
    CandidateManifestError,
    manifest_references,
    parse_manifest,
    validate_git_overlay,
    validate_repo_path,
)

ReferenceAuthenticator = Callable[[Mapping[str, str]], bool]


class CandidateSnapshotError(ValueError):
    """Candidate bytes, repository state, or materialization failed validation."""


@dataclass(frozen=True)
class CandidateSnapshot:
    manifest: Mapping[str, Any]
    manifest_sha256: str
    source_tree: str
    materialized_root: Path

    @property
    def base_commit(self) -> str:
        return str(self.manifest["base"]["head"])

    @property
    def tree(self) -> str:
        return self.source_tree


def _git(root: Path, args: list[str], *, env: Mapping[str, str] | None = None,
         input_bytes: bytes | None = None) -> bytes:
    try:
        result = subprocess.run(["git", *args], cwd=root, env=None if env is None else dict(env),
                                input=input_bytes, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                check=False, stdin=None if input_bytes is not None else subprocess.DEVNULL)
    except OSError as exc:
        raise CandidateSnapshotError(f"cannot execute Git: {args[0]}") from exc
    if result.returncode:
        detail = result.stderr.decode("utf-8", "replace")[-1200:]
        raise CandidateSnapshotError(f"Git command failed ({args[0]}): {detail}")
    return result.stdout


def _git_environment(root: Path, temporary: Path) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    object_dir = _git(root, ["rev-parse", "--git-path", "objects"]).decode().strip()
    objects = Path(object_dir)
    if not objects.is_absolute():
        objects = root / objects
    if not objects.is_dir():
        raise CandidateSnapshotError("repository object database is unavailable")
    env.update({"GIT_OPTIONAL_LOCKS": "0", "GIT_CONFIG_NOSYSTEM": "1", "GIT_ATTR_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": os.devnull, "GIT_INDEX_FILE": str(temporary / "index"),
                "GIT_OBJECT_DIRECTORY": str(temporary / "objects"),
                "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(objects.resolve())})
    return env


def _is_reparse(info: os.stat_result) -> bool:
    return bool(getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _safe_file(root: Path, relative: str) -> tuple[Path, os.stat_result]:
    path = root
    for component in relative.split("/"):
        path = path / component
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or _is_reparse(info):
            raise CandidateSnapshotError(f"symlink or reparse point is not admitted: {relative}")
    if not stat.S_ISREG(info.st_mode):
        raise CandidateSnapshotError(f"candidate path is not a regular file: {relative}")
    return path, info


def _is_operational(child: os.DirEntry[str], relative: str, roots: set[str]) -> bool:
    if relative not in roots:
        return False
    info = child.stat(follow_symlinks=False)
    if not child.is_dir(follow_symlinks=False) or stat.S_ISLNK(info.st_mode) or _is_reparse(info):
        raise CandidateSnapshotError(f"operational root must be a real directory: {relative}")
    return True


def _record_workspace_entry(
    child: os.DirEntry[str], relative: str, protected: set[str], operational: set[str], found: set[str],
    exclude_git: bool,
) -> None:
    if _is_operational(child, relative, operational):
        return
    try:
        validate_repo_path(relative)
    except CandidateManifestError as exc:
        raise CandidateSnapshotError(f"workspace contains an unsafe path: {relative}") from exc
    info = child.stat(follow_symlinks=False)
    if stat.S_ISLNK(info.st_mode) or _is_reparse(info):
        raise CandidateSnapshotError(f"workspace contains an unclassified symlink or reparse point: {relative}")
    if child.is_dir(follow_symlinks=False):
        _walk_workspace(Path(child.path), relative, protected, operational, found, exclude_git)
    elif child.is_file(follow_symlinks=False):
        if relative not in protected:
            found.add(relative)
    else:
        raise CandidateSnapshotError(f"workspace contains an unsupported filesystem object: {relative}")


def _walk_workspace(
    directory: Path, prefix: str, protected: set[str], operational: set[str], found: set[str],
    exclude_git: bool,
) -> None:
    try:
        children = sorted(os.scandir(directory), key=lambda item: item.name)
    except OSError as exc:
        raise CandidateSnapshotError(f"cannot enumerate workspace path: {prefix or '.'}") from exc
    for child in children:
        if exclude_git and not prefix and child.name == ".git":
            continue
        relative = f"{prefix}/{child.name}" if prefix else child.name
        _record_workspace_entry(child, relative, protected, operational, found, exclude_git)


def _scan_workspace(root: Path, manifest: Mapping[str, Any], *, workspace: bool) -> set[str]:
    protected = {row["path"] for row in manifest["workspace_context"]["protected_files"]} if workspace else set()
    operational = set(manifest["workspace_context"]["operational_roots"]) if workspace else set()
    found: set[str] = set()
    _walk_workspace(root, "", protected, operational, found, workspace)
    return found


def _authenticate(manifest: Mapping[str, Any], root: Path,
                  callback: ReferenceAuthenticator | None, *, workspace: bool) -> None:
    for reference in manifest_references(manifest, workspace=workspace):
        if callback is not None:
            valid = callback(reference)
        else:
            try:
                path, _ = _safe_file(root, reference["path"])
                valid = hashlib.sha256(path.read_bytes()).hexdigest() == reference["sha256"]
            except OSError:
                valid = False
        if not valid:
            raise CandidateSnapshotError(f"evidence reference failed authentication: {reference['path']}")


def _verify_files(root: Path, manifest: Mapping[str, Any]) -> None:
    files = {item["path"]: item for item in manifest["files"]}
    for relative, expected in files.items():
        path, info = _safe_file(root, relative)
        content = path.read_bytes()
        if len(content) != expected["bytes"] or hashlib.sha256(content).hexdigest() != expected["sha256"]:
            raise CandidateSnapshotError(f"candidate bytes differ from manifest: {relative}")
        mode = expected["mode"] if os.name == "nt" else "100755" if info.st_mode & stat.S_IXUSR else "100644"
        if mode != expected["mode"]:
            raise CandidateSnapshotError(f"candidate mode differs from manifest: {relative}")


def _construct_tree(
    git_root: Path, source_root: Path, manifest: Mapping[str, Any], env: Mapping[str, str],
    destination: Path | None = None,
) -> str:
    _git(git_root, ["read-tree", "--empty"], env=env)
    blobs: list[tuple[Mapping[str, Any], str]] = []
    entries: list[bytes] = []
    for row in manifest["files"]:
        source, _ = _safe_file(source_root, row["path"])
        content = source.read_bytes()
        if len(content) != row["bytes"] or hashlib.sha256(content).hexdigest() != row["sha256"]:
            raise CandidateSnapshotError(f"candidate bytes changed during tree construction: {row['path']}")
        blob = _git(git_root, ["hash-object", "-w", "--stdin", "--no-filters"], env=env,
                    input_bytes=content).decode("ascii").strip()
        entries.append(f"{row['mode']} {blob}\t{row['path']}\0".encode("utf-8"))
        blobs.append((row, blob))
    if entries:
        _git(git_root, ["update-index", "-z", "--index-info"], env=env, input_bytes=b"".join(entries))
    tree = _git(git_root, ["write-tree"], env=env).decode("ascii").strip()
    if destination is not None:
        for row, blob in blobs:
            target = destination.joinpath(*row["path"].split("/"))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(_git(git_root, ["cat-file", "blob", blob], env=env))
            if os.name != "nt":
                target.chmod(0o755 if row["mode"] == "100755" else 0o644)
    return tree


def _build_and_materialize(root: Path, manifest: Mapping[str, Any], destination: Path) -> str:
    destination = destination.resolve()
    root = root.resolve()
    if destination == root or root in destination.parents or destination in root.parents:
        raise CandidateSnapshotError("materialization destination must be outside the repository")
    if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
        raise CandidateSnapshotError("materialization destination must be empty")
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="candidate-exact-byte-git-") as raw_temp:
        temporary = Path(raw_temp)
        object_path = temporary / "objects"
        object_path.mkdir()
        env = _git_environment(root, temporary)
        tree = _construct_tree(root, root, manifest, env, destination)
        if tree != manifest["source_tree"]:
            raise CandidateSnapshotError("reconstructed exact-byte tree differs from manifest source_tree")
    _verify_files(destination, manifest)
    return tree


def _verify_pinned_repository(root: Path, manifest: Mapping[str, Any]) -> None:
    object_format = _git(root, ["rev-parse", "--show-object-format"]).decode().strip()
    head = _git(root, ["rev-parse", "HEAD"]).decode().strip()
    tree = _git(root, ["rev-parse", "HEAD^{tree}"]).decode().strip()
    if object_format != manifest["object_format"] or head != manifest["base"]["head"] or tree != manifest["base"]["tree"]:
        raise CandidateSnapshotError("repository object format or HEAD/tree differs from pinned base")
    if _git(root, ["diff", "--cached", "--quiet"]).strip():
        raise CandidateSnapshotError("staging must be empty")


def verify_workspace(root: Path, manifest_bytes: bytes, expected_sha256: str,
                     destination: Path, *, authenticate_reference: ReferenceAuthenticator | None = None) -> CandidateSnapshot:
    """Verify a selected live workspace, then materialize its exact source bytes."""
    root = root.resolve()
    if not root.is_dir():
        raise CandidateSnapshotError("repository root does not exist")
    try:
        manifest = parse_manifest(manifest_bytes, expected_sha256)
    except CandidateManifestError as exc:
        raise CandidateSnapshotError(str(exc)) from exc
    _verify_pinned_repository(root, manifest)
    _authenticate(manifest, root, authenticate_reference, workspace=True)
    try:
        raw_tree = _git(root, ["ls-tree", "-r", "-z", "--full-tree", manifest["base"]["tree"]])
        validate_git_overlay(manifest, raw_tree, lambda oid: _git(root, ["cat-file", "blob", oid]))
    except CandidateManifestError as exc:
        raise CandidateSnapshotError(str(exc)) from exc
    actual_paths = _scan_workspace(root, manifest, workspace=True)
    expected_paths = {item["path"] for item in manifest["files"]}
    if actual_paths != expected_paths:
        raise CandidateSnapshotError("workspace source path set differs from complete manifest")
    _verify_files(root, manifest)
    for row in manifest["workspace_context"]["protected_files"]:
        try:
            path, _ = _safe_file(root, row["path"])
            data = path.read_bytes()
        except OSError as exc:
            raise CandidateSnapshotError(f"protected workspace file is unavailable: {row['path']}") from exc
        if len(data) != row["bytes"] or hashlib.sha256(data).hexdigest() != row["sha256"]:
            raise CandidateSnapshotError(f"protected workspace file changed: {row['path']}")
    tree = _build_and_materialize(root, manifest, destination)
    return CandidateSnapshot(manifest, expected_sha256, tree, destination.resolve())


def verify_materialized(root: Path, manifest_bytes: bytes, expected_sha256: str, *,
                        authenticate_reference: ReferenceAuthenticator | None = None) -> CandidateSnapshot:
    """Verify a complete isolated source tree without requiring workspace authority."""
    root = root.resolve()
    try:
        manifest = parse_manifest(manifest_bytes, expected_sha256)
    except CandidateManifestError as exc:
        raise CandidateSnapshotError(str(exc)) from exc
    _authenticate(manifest, root, authenticate_reference, workspace=False)
    actual_paths = _scan_workspace(root, manifest, workspace=False)
    expected_paths = {item["path"] for item in manifest["files"]}
    if actual_paths != expected_paths:
        raise CandidateSnapshotError("materialized source path set differs from complete manifest")
    _verify_files(root, manifest)
    with tempfile.TemporaryDirectory(prefix="candidate-materialized-check-") as scratch:
        scratch_root = Path(scratch)
        clean_env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
        clean_env.update({"GIT_OPTIONAL_LOCKS": "0", "GIT_CONFIG_NOSYSTEM": "1",
                          "GIT_CONFIG_GLOBAL": os.devnull, "GIT_ATTR_NOSYSTEM": "1"})
        _git_init = subprocess.run(["git", "init", "-q", scratch], env=clean_env, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, check=False)
        if _git_init.returncode:
            raise CandidateSnapshotError("cannot initialize isolated materialized-source verifier")
        with tempfile.TemporaryDirectory(prefix="candidate-materialized-objects-") as objects_temp:
            object_root = Path(objects_temp)
            (object_root / "objects").mkdir()
            env = dict(clean_env)
            env.update({
                        "GIT_OBJECT_DIRECTORY": str(object_root / "objects"), "GIT_INDEX_FILE": str(object_root / "index")})
            tree = _construct_tree(scratch_root, root, manifest, env)
    if tree != manifest["source_tree"]:
        raise CandidateSnapshotError("materialized source tree differs from manifest source_tree")
    return CandidateSnapshot(manifest, expected_sha256, tree, root)


__all__ = ["CandidateSnapshot", "CandidateSnapshotError", "ReferenceAuthenticator", "verify_materialized", "verify_workspace"]

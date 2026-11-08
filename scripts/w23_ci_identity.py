"""Record and compare tracked source identities without refreshing the Git index."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any


def _git(root: Path, *args: str, input: bytes | None = None) -> bytes:
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0", GIT_NO_REPLACE_OBJECTS="1")
    return subprocess.check_output(["git", *args], cwd=root, env=env, input=input)


def _assert_index_matches_head(root: Path) -> None:
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0", GIT_NO_REPLACE_OBJECTS="1")
    result = subprocess.run(
        ["git", "diff-index", "--cached", "--quiet", "HEAD", "--"],
        cwd=root, env=env, capture_output=True,
    )
    if result.returncode != 0:
        raise ValueError("Git index differs from authenticated commit tree")


def _stat_identity(path: Path) -> tuple[int, ...]:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise ValueError("Git checkout contains a symlink or reparse point: " + str(path))
    # Windows' lstat/fstat report creation/change timestamps differently.
    # The file identity, type, size, and write timestamp remain comparable.
    return (info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode), info.st_size, info.st_mtime_ns)


def _checkout_path(root: Path, relative: str) -> Path:
    path = root
    parts = relative.split("/")
    for index, part in enumerate(parts):
        path = path / part
        identity = _stat_identity(path)
        if index < len(parts) - 1 and not stat.S_ISDIR(identity[2]):
            raise ValueError("Git checkout path has a non-directory parent: " + relative)
    return path


def _read_checkout_file(root: Path, relative: str) -> tuple[bytes, tuple[int, ...]]:
    path = _checkout_path(root, relative)
    before = _stat_identity(path)
    if not stat.S_ISREG(before[2]):
        raise ValueError("Git checkout contains a non-regular file: " + relative)
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        data = stream.read()
        closed = os.fstat(stream.fileno())
    after = _checkout_path(root, relative)
    after_identity = _stat_identity(after)
    if os.name == "nt":
        # Windows may report different file IDs/timestamps through path and
        # handle APIs. Require a stable regular handle and stable path snapshot.
        stable_handle = (stat.S_ISREG(opened.st_mode) and stat.S_ISREG(closed.st_mode)
                         and opened.st_size == len(data) == closed.st_size
                         and opened.st_mtime_ns == closed.st_mtime_ns)
        stable_path = before == after_identity
    else:
        opened_identity = (opened.st_dev, opened.st_ino, stat.S_IFMT(opened.st_mode),
                           opened.st_size, opened.st_mtime_ns)
        closed_identity = (closed.st_dev, closed.st_ino, stat.S_IFMT(closed.st_mode),
                           closed.st_size, closed.st_mtime_ns)
        stable_handle = opened_identity == closed_identity
        stable_path = before == opened_identity == after_identity
    if not stable_handle or not stable_path:
        raise ValueError("Git checkout changed while authenticating: " + relative)
    return data, after_identity


def assert_unchanged(before: dict[str, Any], after: dict[str, Any]) -> None:
    if before != after:
        raise ValueError("tracked source changed during acceptance")


def _capture_index_files(
    root: Path,
) -> tuple[list[tuple[str, str, str]], list[tuple[str, str, tuple[int, ...]]]]:
    raw = _git(root, "ls-files", "--stage", "-z")
    entries: list[tuple[str, str, str]] = []
    snapshots: list[tuple[str, str, tuple[int, ...]]] = []
    seen_paths: set[str] = set()
    folded_paths: set[str] = set()
    for entry in raw.split(b"\0"):
        if not entry:
            continue
        header, path = entry.split(b"\t", 1)
        mode, oid, stage = header.decode().split()
        if stage != "0":
            raise ValueError("unmerged source index")
        if mode not in {"100644", "100755"}:
            raise ValueError("unsupported authenticated Git mode: " + mode)
        relative = path.decode("utf-8", errors="strict")
        if "\n" in relative or "\r" in relative:
            raise ValueError("checkout path cannot be safely passed to Git filters: " + repr(relative))
        folded = relative.casefold()
        if relative in seen_paths or folded in folded_paths:
            raise ValueError("Git index contains duplicate or case-colliding paths: " + relative)
        seen_paths.add(relative)
        folded_paths.add(folded)
        data, identity = _read_checkout_file(root, relative)
        snapshots.append((relative, hashlib.sha256(data).hexdigest(), identity))
        entries.append((relative, mode, oid))
    return entries, snapshots


def _read_authenticated_blob(stream: io.BytesIO, expected_oid: str, algorithm: str) -> bytes:
    header = stream.readline().decode("ascii", errors="strict").split()
    if len(header) != 3 or header[:2] != [expected_oid, "blob"]:
        raise ValueError("Git blob response differs from authenticated index entry")
    size = int(header[2])
    if size < 0:
        raise ValueError("Git blob response has a negative size")
    data = stream.read(size)
    if len(data) != size or stream.read(1) != b"\n":
        raise ValueError("Git blob response is truncated or malformed")
    actual_oid = hashlib.new(algorithm, b"blob " + str(size).encode() + b"\0" + data).hexdigest()
    if actual_oid != expected_oid:
        raise ValueError("Git blob bytes differ from authenticated object identity")
    return data


def _authenticated_git_blobs(root: Path, entries: list[tuple[str, str, str]]) -> list[bytes]:
    algorithm = _git(root, "rev-parse", "--show-object-format").decode().strip()
    requests = "".join(oid + "\n" for _, _, oid in entries).encode("ascii")
    stream = io.BytesIO(_git(root, "cat-file", "--batch", input=requests))
    blobs = [_read_authenticated_blob(stream, oid, algorithm) for _, _, oid in entries]
    if stream.read():
        raise ValueError("Git blob response contains unexpected trailing bytes")
    return blobs


def _authenticate_checkout_blobs(
    root: Path,
    head: str,
    entries: list[tuple[str, str, str]],
    snapshots: list[tuple[str, str, tuple[int, ...]]],
) -> None:
    blobs = _authenticated_git_blobs(root, entries)
    for (relative, _, oid), (_, checkout_sha256, _), blob in zip(entries, snapshots, blobs, strict=True):
        if hashlib.sha256(blob).hexdigest() == checkout_sha256:
            continue
        # Cleaning a checkout is not an inverse of historical blob storage.
        # Derive the expected materialization from the authenticated blob,
        # pinning attributes to the commit rather than mutable worktree files.
        expected = _git(root, "--attr-source=" + head, "cat-file", "--filters", "--path=" + relative, oid)
        if hashlib.sha256(expected).hexdigest() != checkout_sha256:
            raise ValueError("Git checkout changed authenticated bytes: " + relative)


def _verify_checkout_snapshots(
    root: Path,
    entries: list[tuple[str, str, str]],
    snapshots: list[tuple[str, str, tuple[int, ...]]],
) -> list[dict[str, str]]:
    files = []
    for (relative, mode, _), (_, previous_sha256, previous_identity) in zip(entries, snapshots, strict=True):
        data, current_identity = _read_checkout_file(root, relative)
        if hashlib.sha256(data).hexdigest() != previous_sha256 or current_identity != previous_identity:
            raise ValueError("Git checkout changed while authenticating: " + relative)
        files.append({"path": relative, "mode": mode, "sha256": previous_sha256})
    return files


def capture(root: Path) -> dict[str, Any]:
    root_identity = _stat_identity(root)
    if not stat.S_ISDIR(root_identity[2]):
        raise ValueError("Git checkout root is not a regular directory")
    head = _git(root, "rev-parse", "--verify", "HEAD^{commit}").decode().strip()
    head_tree = _git(root, "rev-parse", "--verify", "HEAD^{tree}").decode().strip()
    _assert_index_matches_head(root)

    entries, snapshots = _capture_index_files(root)
    _authenticate_checkout_blobs(root, head, entries, snapshots)
    files = _verify_checkout_snapshots(root, entries, snapshots)

    final_head = _git(root, "rev-parse", "--verify", "HEAD^{commit}").decode().strip()
    final_head_tree = _git(root, "rev-parse", "--verify", "HEAD^{tree}").decode().strip()
    _assert_index_matches_head(root)
    if (final_head, final_head_tree) != (head, head_tree):
        raise ValueError("Git commit or index identity changed while authenticating")
    return {"head_commit": head, "source_tree": head_tree, "files": files}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["before", "after"])
    args = parser.parse_args()
    evidence = Path(os.environ["RUNNER_TEMP"]) / "w23-evidence"
    evidence.mkdir(exist_ok=True)
    environment = {"system": platform.system(), "kernel": platform.release(), "python": sys.version,
                   "disk": dict(zip(("total", "used", "free"), shutil.disk_usage(Path.cwd()), strict=True))}
    if os.name != "nt":
        environment["filesystem"] = subprocess.check_output(
            ["findmnt", "-T", os.environ["RUNNER_TEMP"], "-o", "SOURCE,FSTYPE,TARGET"], text=True)
    (evidence / ("environment_" + args.phase + ".json")).write_text(
        json.dumps(environment, indent=2) + "\n", encoding="utf-8")
    data = capture(Path.cwd())
    (evidence / ("source_" + args.phase + ".json")).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    if args.phase == "after":
        assert_unchanged(json.loads((evidence / "source_before.json").read_bytes()), data)
    print(json.dumps({"state": "OBSERVED PASS", "head_commit": data["head_commit"],
                      "source_tree": data["source_tree"], "files": len(data["files"])}))


if __name__ == "__main__":
    main()

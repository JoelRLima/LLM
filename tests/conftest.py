"""Shared immutable source snapshots for historical evidence tests."""

from __future__ import annotations

import csv
import os
import re
import shutil
import stat
import subprocess
import tarfile
import uuid
from pathlib import Path, PurePosixPath

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
W23_SUCCESSOR_CHECKPOINT = "9b5a2ddc2645430142e3c1ecaf6ef95c41e40406"
PRIVATE_SNAPSHOT_TEST_MODULES = {
    "test_candidate_manifest.py",
    "test_candidate_manifest_regressions.py",
}
PRIVATE_SNAPSHOT_TEST_IDS = {
    "tests/unit/test_wave18_build_driver.py::test_explicit_candidate_materialization_is_pinned_after_worktree_mutation",
}
_WINDOWS_USER_SID: str | None = None


def _windows_user_sid() -> str:
    global _WINDOWS_USER_SID
    if _WINDOWS_USER_SID is not None:
        return _WINDOWS_USER_SID
    result = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"], check=True,
                            capture_output=True, text=True)
    rows = list(csv.reader(line for line in result.stdout.splitlines() if line.strip()))
    if len(rows) != 1 or len(rows[0]) != 2 or not re.fullmatch(r"S-\d+(?:-\d+)+", rows[0][1]):
        raise RuntimeError("cannot establish exact Windows pytest temp owner SID")
    _WINDOWS_USER_SID = rows[0][1]
    return _WINDOWS_USER_SID


def _set_private_windows_temp_acl(path: Path) -> None:
    """Give only the test owner, SYSTEM and Administrators rights on pytest temp dirs."""
    if os.name != "nt":
        return
    info = path.lstat()
    if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
            or getattr(info, "st_file_attributes", 0) & 0x400):
        raise RuntimeError("pytest temp ACL target is not a real directory")
    result = subprocess.run(
        ["icacls", str(path), "/inheritance:r", "/grant:r", f"*{_windows_user_sid()}:(OI)(CI)(F)",
         "/grant", "*S-1-5-18:(OI)(CI)(F)", "/grant", "*S-1-5-32-544:(OI)(CI)(F)"],
        check=False, capture_output=True, text=True,
    )
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"cannot establish protected pytest temp ACL: {detail}")


@pytest.fixture
def tmp_path(request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Give each test a private temp dir; isolate only ACL-sensitive snapshot modules."""
    name = re.sub(r"[\W]", "_", request.node.name)[:30]
    configured_root = os.environ.get("W23_PRIVATE_TEST_ROOT")
    needs_private_snapshot_root = (
        Path(str(request.node.path)).name in PRIVATE_SNAPSHOT_TEST_MODULES
        or request.node.nodeid in PRIVATE_SNAPSHOT_TEST_IDS
    )
    if os.name == "nt" and configured_root and needs_private_snapshot_root:
        root = Path(configured_root)
        if not root.is_absolute() or not root.is_dir():
            raise RuntimeError("W23 private pytest root must be an existing absolute directory")
        path = root / f"{name}-{uuid.uuid4().hex}"
        path.mkdir()
        _set_private_windows_temp_acl(path)
        return path
    path = tmp_path_factory.mktemp(name, numbered=True)
    _set_private_windows_temp_acl(path)
    return path


@pytest.fixture(scope="session")
def w23_checkpoint_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Materialize the commit whose bytes the frozen W23 successor authenticates."""
    temporary_root = tmp_path_factory.mktemp("w23-successor-checkpoint")
    archive_path = temporary_root / "checkpoint.tar"
    checkout = temporary_root / "checkout"
    checkout.mkdir()
    subprocess.run(
        [
            "git",
            "-C",
            str(REPOSITORY_ROOT),
            "archive",
            "--format=tar",
            f"--output={archive_path}",
            W23_SUCCESSOR_CHECKPOINT,
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )

    seen: set[str] = set()
    with tarfile.open(archive_path, mode="r:") as source_archive:
        for member in source_archive.getmembers():
            name = member.name.rstrip("/")
            relative = PurePosixPath(name)
            if (
                not name
                or relative.is_absolute()
                or "\\" in name
                or any(part in {"", ".", ".."} or ":" in part for part in relative.parts)
                or name in seen
            ):
                raise AssertionError(f"unsafe or duplicate checkpoint archive path: {name!r}")
            seen.add(name)
            destination = checkout.joinpath(*relative.parts)
            if member.isdir():
                destination.mkdir(parents=True, exist_ok=True)
                continue
            if not member.isfile():
                raise AssertionError(f"unsupported checkpoint archive entry: {name!r}")
            source = source_archive.extractfile(member)
            if source is None:
                raise AssertionError(f"checkpoint archive entry has no content: {name!r}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            with source, destination.open("xb") as output:
                shutil.copyfileobj(source, output)

    archive_path.unlink()
    return checkout

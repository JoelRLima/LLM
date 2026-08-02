import os
import subprocess
import sys
from pathlib import Path

import pytest

from agent.tools.extension_catalog_errors import CatalogLockBusyError, CatalogLockError
from agent.tools.extension_catalog_lock import ExtensionCatalogLock


def test_lock_acquire_release_and_reacquire(tmp_path: Path) -> None:
    path = tmp_path / "catalog.json.lock"
    first = ExtensionCatalogLock(path)
    second = ExtensionCatalogLock(path)

    first.acquire()
    try:
        with pytest.raises(CatalogLockBusyError):
            second.acquire()
    finally:
        first.release()

    second.acquire()
    second.release()
    assert path.exists()


def test_lock_is_idempotent_for_same_owner(tmp_path: Path) -> None:
    lock = ExtensionCatalogLock(tmp_path / "catalog.lock")

    lock.acquire()
    lock.acquire()
    lock.release()
    lock.release()


def test_lock_context_releases_after_exception(tmp_path: Path) -> None:
    path = tmp_path / "catalog.lock"
    with pytest.raises(RuntimeError):
        with ExtensionCatalogLock(path):
            raise RuntimeError("failure")

    replacement = ExtensionCatalogLock(path)
    replacement.acquire()
    replacement.release()


def test_lock_excludes_child_process_and_releases_for_child(tmp_path: Path) -> None:
    path = tmp_path / "catalog.lock"
    child_code = """
import sys
from agent.tools.extension_catalog_errors import CatalogLockBusyError
from agent.tools.extension_catalog_lock import ExtensionCatalogLock
lock = ExtensionCatalogLock(sys.argv[1])
try:
    lock.acquire()
except CatalogLockBusyError:
    print("BUSY")
else:
    print("ACQUIRED")
    lock.release()
"""
    parent = ExtensionCatalogLock(path)
    parent.acquire()
    try:
        busy = subprocess.run(
            [sys.executable, "-c", child_code, str(path)],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        assert busy.stdout.strip() == "BUSY"
    finally:
        parent.release()

    acquired = subprocess.run(
        [sys.executable, "-c", child_code, str(path)],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    assert acquired.stdout.strip() == "ACQUIRED"


@pytest.mark.skipif(os.name == "nt", reason="fcntl é específico de POSIX neste teste")
def test_lock_release_failure_is_typed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lock = ExtensionCatalogLock(tmp_path / "catalog.lock")
    lock.acquire()
    monkeypatch.setattr("fcntl.flock", lambda *args: (_ for _ in ()).throw(OSError("release")))

    with pytest.raises(CatalogLockError):
        lock.release()


def test_context_preserves_body_error_when_release_also_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock = ExtensionCatalogLock(tmp_path / "catalog.lock")
    lock.acquire()
    real_release = lock.release

    def fail_release() -> None:
        real_release()
        raise CatalogLockError("release")

    monkeypatch.setattr(lock, "release", fail_release)

    with pytest.raises(ValueError, match="body") as caught:
        with lock:
            raise ValueError("body")

    assert any("release" in note for note in getattr(caught.value, "__notes__", []))

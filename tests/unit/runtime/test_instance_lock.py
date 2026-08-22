from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from threading import Barrier, Thread
from types import SimpleNamespace

import pytest

from agent.runtime import instance_lock as instance_lock_module
from agent.runtime import lock_filesystem
from agent.runtime.file_lock import OPEN_BINARY
from agent.runtime.instance_lock import InstanceLock, InstanceLockError
from agent.runtime.process_identity import (
    OwnerStatus,
    ProcessOwnerLiveness,
    current_process_start_id,
)


@dataclass
class _FakeLiveness:
    default: OwnerStatus = OwnerStatus.DEAD
    statuses: dict[int, OwnerStatus] = field(default_factory=dict)

    def check(self, pid: int, process_start_id: str | None) -> OwnerStatus:
        del process_start_id
        return self.statuses.get(pid, self.default)


def _write_record(
    path: Path,
    *,
    pid: int,
    token: str = "old-token",
    start_id: str | None = None,
) -> bytes:
    document: dict[str, object] = {
        "pid": pid,
        "token": token,
        "created_at": "2026-08-21T14:37:01.266551+00:00",
    }
    if start_id is not None:
        document["process_start_id"] = start_id
    raw = (json.dumps(document, sort_keys=True) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return raw


def test_basic_acquisition_payload_and_own_release(tmp_path: Path) -> None:
    path = tmp_path / "state" / "application.lock"
    lock = InstanceLock.create(path)

    lock.acquire()
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["pid"] == os.getpid()
    assert payload["token"] == lock.token
    assert payload["created_at"]
    assert "process_start_id" in payload
    lock.release()
    assert not path.exists()


def test_second_live_owner_fails_and_first_record_remains(tmp_path: Path) -> None:
    path = tmp_path / "application.lock"
    first = InstanceLock.create(path)
    first.acquire()
    before = path.read_bytes()
    second = InstanceLock.create(path)

    try:
        with pytest.raises(InstanceLockError, match="em uso"):
            second.acquire()
        second.release()
        assert path.read_bytes() == before
    finally:
        first.release()


def test_stale_pid_is_reclaimed_with_a_fresh_token(tmp_path: Path) -> None:
    path = tmp_path / "application.lock"
    _write_record(path, pid=2176)
    lock = InstanceLock.create(path, owner_liveness=_FakeLiveness())

    lock.acquire()
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["pid"] == os.getpid()
    assert payload["token"] == lock.token
    assert payload["token"] != "old-token"
    lock.release()


def test_exact_manual_orphaned_workspace_condition_recovers(tmp_path: Path) -> None:
    path = tmp_path / "testando-36a2efab8468" / "application.lock"
    _write_record(path, pid=2176, token="manual-owner-token")

    lock = InstanceLock.create(path, owner_liveness=_FakeLiveness())
    lock.acquire()
    assert json.loads(path.read_text(encoding="utf-8"))["token"] == lock.token
    lock.release()


def test_non_owner_release_cannot_remove_another_lock(tmp_path: Path) -> None:
    path = tmp_path / "application.lock"
    owner = InstanceLock.create(path)
    owner.acquire()
    before = path.read_bytes()
    assert owner._record is not None
    non_owner = InstanceLock.create(path)
    non_owner._acquired = True
    non_owner._descriptor = os.open(path, OPEN_BINARY | os.O_RDWR)
    non_owner._record = owner._record

    non_owner.release()

    assert path.read_bytes() == before
    owner.release()


def test_two_stale_recovery_contenders_have_one_winner(tmp_path: Path) -> None:
    path = tmp_path / "application.lock"
    _write_record(path, pid=2176)
    start = Barrier(2)
    winners: list[InstanceLock] = []
    failures: list[InstanceLockError] = []

    def contend() -> None:
        lock = InstanceLock.create(path, owner_liveness=_FakeLiveness())
        start.wait()
        try:
            lock.acquire()
            winners.append(lock)
        except InstanceLockError as exc:
            failures.append(exc)

    threads = [Thread(target=contend), Thread(target=contend)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    try:
        assert all(not thread.is_alive() for thread in threads)
        assert len(winners) == 1
        assert len(failures) == 1
    finally:
        for lock in winners:
            lock.release()


def test_replacement_after_observation_is_not_deleted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "application.lock"
    _write_record(path, pid=2176, token="stale")
    replacement = tmp_path / "replacement.lock"
    replacement_raw = _write_record(replacement, pid=os.getpid(), token="replacement")
    liveness = _FakeLiveness(
        statuses={2176: OwnerStatus.DEAD, os.getpid(): OwnerStatus.ALIVE}
    )
    lock = InstanceLock.create(path, owner_liveness=liveness)
    original_same_record = lock._same_record
    replaced = False

    def replace_before_recheck(descriptor: int, observed: object) -> bool:
        nonlocal replaced
        if not replaced:
            lock._close_plain_descriptor(descriptor)
            os.replace(replacement, path)
            replaced = True
        return original_same_record(descriptor, observed)  # type: ignore[arg-type]

    monkeypatch.setattr(lock, "_same_record", replace_before_recheck)

    with pytest.raises(InstanceLockError, match="em uso"):
        lock.acquire()

    assert path.read_bytes() == replacement_raw


@pytest.mark.parametrize(
    "raw",
    (
        b"{",
        b'{"pid": 2176, "token": "missing-created-at"}',
        b'{"pid": 2176, "created_at": "2026-08-21T00:00:00+00:00"}',
    ),
)
def test_malformed_or_incomplete_lock_fails_closed(tmp_path: Path, raw: bytes) -> None:
    path = tmp_path / "application.lock"
    path.write_bytes(raw)
    lock = InstanceLock.create(path, owner_liveness=_FakeLiveness())

    with pytest.raises(InstanceLockError, match="segurança"):
        lock.acquire()
    assert path.read_bytes() == raw


def test_live_pid_is_not_reclaimed(tmp_path: Path) -> None:
    path = tmp_path / "application.lock"
    raw = _write_record(path, pid=os.getpid(), start_id=current_process_start_id())
    lock = InstanceLock.create(path)

    with pytest.raises(InstanceLockError, match="em uso"):
        lock.acquire()
    assert path.read_bytes() == raw


def test_pid_reuse_identity_mismatch_is_treated_as_dead() -> None:
    identity = current_process_start_id()
    if identity is None:
        pytest.skip("process-start identity is unavailable on this platform")
    liveness = ProcessOwnerLiveness()

    assert liveness.check(os.getpid(), identity) is OwnerStatus.ALIVE
    assert liveness.check(os.getpid(), f"different:{identity}") is OwnerStatus.DEAD
    assert liveness.check(os.getpid(), None) is OwnerStatus.ALIVE


def test_owner_death_without_release_is_recoverable(tmp_path: Path) -> None:
    path = tmp_path / "application.lock"
    script = (
        "import os, sys; "
        "from agent.runtime.instance_lock import InstanceLock; "
        "lock = InstanceLock.create(sys.argv[1]); lock.acquire(); os._exit(0)"
    )
    child = subprocess.Popen([sys.executable, "-c", script, str(path)])
    assert child.wait(timeout=10) == 0
    assert path.exists()

    recovered = InstanceLock.create(path)
    recovered.acquire()
    recovered.release()
    assert not path.exists()


@pytest.mark.parametrize("platform_name", ("windows" if os.name == "nt" else "linux",))
def test_supported_platform_backend_acquires_and_releases(
    tmp_path: Path,
    platform_name: str,
) -> None:
    path = tmp_path / f"{platform_name}-application.lock"
    lock = InstanceLock.create(path)
    lock.acquire()
    lock.release()
    assert not path.exists()


def _create_symlink_or_skip(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink/reparse primitive unavailable: {exc}")


def test_lock_final_symlink_is_rejected_without_touching_target(tmp_path: Path) -> None:
    target = tmp_path / "external-target.txt"
    target.write_bytes(b"external sentinel")
    path = tmp_path / "application.lock"
    _create_symlink_or_skip(path, target)

    with pytest.raises(InstanceLockError, match="seguran"):
        InstanceLock.create(path).acquire()

    assert target.read_bytes() == b"external sentinel"
    assert path.is_symlink()


def test_guard_final_symlink_is_rejected_without_touching_target(tmp_path: Path) -> None:
    path = tmp_path / "application.lock"
    target = tmp_path / "external-guard-target.txt"
    target.write_bytes(b"external guard sentinel")
    _create_symlink_or_skip(path.with_name("application.lock.guard"), target)

    with pytest.raises(InstanceLockError, match="seguran"):
        InstanceLock.create(path).acquire()

    assert target.read_bytes() == b"external guard sentinel"
    assert not path.exists()


def test_descriptor_identity_divergence_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "application.lock"
    path.write_bytes(b"regular entry")
    real_match = lock_filesystem.descriptor_matches_path

    def diverge(candidate: Path, descriptor: int) -> bool:
        del candidate, descriptor
        return False

    monkeypatch.setattr(lock_filesystem, "descriptor_matches_path", diverge)
    with pytest.raises(lock_filesystem.UnsafeLockPathError):
        lock_filesystem.open_verified(path, OPEN_BINARY | os.O_RDWR)
    monkeypatch.setattr(lock_filesystem, "descriptor_matches_path", real_match)
    assert path.read_bytes() == b"regular entry"


def test_reparse_marked_regular_entry_is_not_safe() -> None:
    entry = SimpleNamespace(
        st_mode=stat.S_IFREG,
        st_file_attributes=getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400),
    )

    assert not lock_filesystem.is_safe_regular(entry)  # type: ignore[arg-type]


def test_crash_after_guard_before_publication_is_recoverable(tmp_path: Path) -> None:
    path = tmp_path / "application.lock"
    script = (
        "import os, sys; "
        "from agent.runtime.instance_lock import InstanceLock; "
        "InstanceLock._create_private_descriptor = lambda self, path: os._exit(0); "
        "InstanceLock.create(sys.argv[1]).acquire()"
    )
    child = subprocess.run([sys.executable, "-c", script, str(path)], check=False, timeout=10)
    assert child.returncode == 0
    assert not path.exists()

    recovered = InstanceLock.create(path)
    recovered.acquire()
    recovered.release()
    assert not path.exists()


def test_crash_after_private_record_before_publication_is_recoverable(tmp_path: Path) -> None:
    path = tmp_path / "application.lock"
    script = (
        "import os, sys\n"
        "from agent.runtime.instance_lock import InstanceLock\n"
        "write = InstanceLock._write_payload\n"
        "def crash(self, descriptor, payload):\n"
        "    write(self, descriptor, payload)\n"
        "    os._exit(0)\n"
        "InstanceLock._write_payload = crash\n"
        "InstanceLock.create(sys.argv[1]).acquire()\n"
    )
    child = subprocess.run([sys.executable, "-c", script, str(path)], check=False, timeout=10)
    assert child.returncode == 0
    assert not path.exists()
    assert list(path.parent.glob(f".{path.name}.*.tmp"))

    recovered = InstanceLock.create(path)
    recovered.acquire()
    recovered.release()
    assert not path.exists()


def test_crash_after_publication_before_acquire_return_recovers(tmp_path: Path) -> None:
    path = tmp_path / "application.lock"
    script = (
        "import os, sys\n"
        "import agent.runtime.instance_lock as module\n"
        "link = module.os.link\n"
        "def publish(source, destination):\n"
        "    link(source, destination)\n"
        "    os._exit(0)\n"
        "module.os.link = publish\n"
        "module.InstanceLock.create(sys.argv[1]).acquire()\n"
    )
    child = subprocess.run([sys.executable, "-c", script, str(path)], check=False, timeout=10)
    assert child.returncode == 0
    assert path.exists()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["pid"]
    assert payload["token"]

    recovered = InstanceLock.create(path)
    recovered.acquire()
    recovered.release()
    assert not path.exists()


def test_publication_race_does_not_overwrite_existing_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "application.lock"
    lock = InstanceLock.create(path, owner_liveness=_FakeLiveness())
    real_link = instance_lock_module.os.link
    original_inspect = lock._inspect_existing_lock
    injected_raw: bytes | None = None
    inspected = False

    def racing_link(source: Path, destination: Path) -> None:
        nonlocal injected_raw
        if injected_raw is None:
            injected_raw = _write_record(destination, pid=2176, token="racing-owner")
        real_link(source, destination)

    def inspect() -> bool:
        nonlocal inspected
        if injected_raw is not None:
            assert path.read_bytes() == injected_raw
            inspected = True
        return original_inspect()

    monkeypatch.setattr(instance_lock_module.os, "link", racing_link)
    monkeypatch.setattr(lock, "_inspect_existing_lock", inspect)
    lock.acquire()
    try:
        assert injected_raw is not None
        assert inspected
        assert json.loads(path.read_text(encoding="utf-8"))["token"] == lock.token
    finally:
        lock.release()


def test_hard_link_publication_is_create_only(tmp_path: Path) -> None:
    source = tmp_path / "private-record"
    destination = tmp_path / "application.lock"
    source.write_bytes(b"new owner")
    destination.write_bytes(b"existing owner")

    with pytest.raises(FileExistsError):
        os.link(source, destination)
    assert destination.read_bytes() == b"existing owner"

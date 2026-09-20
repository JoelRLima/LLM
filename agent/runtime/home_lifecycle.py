"""Home-level lifecycle leases for W19 bootstrap and maintenance."""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import stat
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator, Mapping, cast

from agent.runtime.file_lock import OPEN_BINARY, lock_descriptor, unlock_descriptor
from agent.runtime.filesystem_primitives import FinalPathInspection, inspect_final_path
from agent.runtime.lock_filesystem import UnsafeLockPathError, descriptor_matches_path, open_verified
from agent.runtime.process_identity import (
    OwnerLiveness,
    OwnerStatus,
    ProcessOwnerLiveness,
    current_process_start_id,
)
from agent.runtime.storage_contracts import StorageMaintenanceError, validate_home_lease


def _coordination_dir(home: Path) -> Path:
    return home.parent / f".{home.name}.lifecycle"
def _guard_path(home: Path) -> Path:
    return _coordination_dir(home) / "guard.lock"
def _leases_dir(home: Path) -> Path:
    return _coordination_dir(home) / "leases"
def _home_fingerprint(home: Path) -> str:
    return hashlib.sha256(str(home.resolve()).encode("utf-8")).hexdigest()
def _close_descriptor(descriptor: int) -> None:
    unlock_descriptor(descriptor)
    try:
        os.close(descriptor)
    except OSError:
        pass
def _ensure_safe_directory(path: Path) -> None:
    current = path
    chain: list[Path] = []
    while True:
        chain.append(current)
        if current == current.parent:
            break
        current = current.parent
    for candidate in reversed(chain):
        try:
            inspection = inspect_final_path(candidate)
        except OSError as exc:
            raise StorageMaintenanceError(
                "MAINTENANCE_HOME_LIVENESS_INDETERMINATE", str(candidate)
            ) from exc
        if not inspection.exists:
            continue
        metadata = inspection.metadata
        if inspection.is_link_like or metadata is None or not stat.S_ISDIR(metadata.st_mode):
            raise StorageMaintenanceError(
                "MAINTENANCE_HOME_LIVENESS_INDETERMINATE", str(candidate)
            )
class HomeLifecycleLease:
    """One live or transient lease on a canonical application home."""
    def __init__(self, home: Path, *, transient: bool, owner_liveness: OwnerLiveness | None = None) -> None:
        self.home = home.resolve()
        self.transient = transient
        self.token = secrets.token_hex(16)
        self._owner_liveness = owner_liveness or ProcessOwnerLiveness()
        self._guard_descriptor: int | None = None
        self._published = False
        self._activated = False
    @classmethod
    def begin_startup(cls, home: Path, *, owner_liveness: OwnerLiveness | None = None) -> "HomeLifecycleLease":
        lease = cls(Path(home), transient=False, owner_liveness=owner_liveness)
        lease._begin()
        return lease
    @classmethod
    def begin_transient(cls, home: Path, *, owner_liveness: OwnerLiveness | None = None) -> "HomeLifecycleLease":
        lease = cls(Path(home), transient=True, owner_liveness=owner_liveness)
        lease._begin()
        return lease
    @property
    def lease_path(self) -> Path:
        return _leases_dir(self.home) / f"{self.token}.json"
    def _begin(self) -> None:
        try:
            _ensure_safe_directory(_coordination_dir(self.home))
            _leases_dir(self.home).mkdir(parents=True, exist_ok=True)
            self._guard_descriptor = _acquire_guard(self.home)
            payload = {
                "schema_version": 1,
                "pid": os.getpid(),
                "process_start_id": current_process_start_id(),
                "token": self.token,
                "home_fingerprint": _home_fingerprint(self.home),
                "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            }
            _publish_lease(self.lease_path, payload, self._record_published)
        except BaseException as original:
            try:
                self.close()
            except BaseException as cleanup:
                raise cleanup from original
            raise
    def activate(self) -> None:
        if self.transient:
            raise RuntimeError("Um lease transient nao pode ser ativado.")
        if not self._published:
            raise RuntimeError("Lease nao publicado.")
        if self._activated:
            return
        self._activated = True
        self._release_guard()
    def _record_published(self) -> None:
        self._published = True
    def close(self) -> None:
        try:
            if self._published:
                _remove_lease_if_owned(self.lease_path, self.token)
                self._published = False
        finally:
            self._release_guard()
    def _release_guard(self) -> None:
        descriptor = self._guard_descriptor
        self._guard_descriptor = None
        if descriptor is not None:
            _close_descriptor(descriptor)
    def __enter__(self) -> "HomeLifecycleLease":
        return self
    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        del exc_type, exc, traceback
        self.close()
def _acquire_guard(home: Path) -> int:
    path = _guard_path(home)
    _ensure_safe_directory(path.parent)
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = OPEN_BINARY | os.O_CREAT | os.O_RDWR | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOINHERIT", 0)
    descriptor: int | None = None
    try:
        descriptor = open_verified(path, flags)
        lock_descriptor(descriptor, blocking=True)
        if not descriptor_matches_path(path, descriptor):
            raise StorageMaintenanceError("MAINTENANCE_HOME_LIVENESS_INDETERMINATE")
        if os.fstat(descriptor).st_size == 0:
            os.lseek(descriptor, 0, os.SEEK_SET)
            os.write(descriptor, b"\0")
            os.fsync(descriptor)
        return descriptor
    except StorageMaintenanceError:
        if descriptor is not None:
            _close_descriptor(descriptor)
        raise
    except (OSError, UnsafeLockPathError) as exc:
        if descriptor is not None:
            _close_descriptor(descriptor)
        raise StorageMaintenanceError(
            "MAINTENANCE_HOME_LIVENESS_INDETERMINATE",
            f"lifecycle guard unavailable: {path}",
        ) from exc
def _publish_lease(path: Path, payload: Mapping[str, object], record_published: Callable[[], None]) -> None:
    raw = (json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    descriptor: int | None = None
    expected: os.stat_result | None = None
    publication_attempted = False
    try:
        flags = OPEN_BINARY | os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOINHERIT", 0)
        descriptor = os.open(temporary, flags, 0o600)
        written = 0
        while written < len(raw):
            written += os.write(descriptor, raw[written:])
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        expected = os.stat(temporary, follow_symlinks=False)
        inspection = inspect_final_path(path)
        if inspection.exists:
            raise StorageMaintenanceError("MAINTENANCE_HOME_LIVENESS_INDETERMINATE", str(path))
        publication_attempted = True
        os.link(temporary, path)
        record_published()
    except StorageMaintenanceError:
        raise
    except BaseException as exc:
        _reconcile_failed_lease_publication(path, expected if publication_attempted else None, exc)
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
def _remove_published_lease_if_owned_or_absent(path: Path, expected: os.stat_result) -> None:
    inspection = _inspect_lease_path(path)
    if not inspection.exists:
        return
    current = inspection.metadata
    if (
        inspection.is_link_like
        or current is None
        or not stat.S_ISREG(current.st_mode)
        or not os.path.samestat(current, expected)
    ):
        raise StorageMaintenanceError("MAINTENANCE_HOME_LIVENESS_INDETERMINATE", str(path))
    try:
        path.unlink()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise StorageMaintenanceError("MAINTENANCE_HOME_LIVENESS_INDETERMINATE", str(path)) from exc
def _reconcile_failed_lease_publication(path: Path, expected: os.stat_result | None, original: BaseException) -> None:
    if expected is not None:
        try:
            _remove_published_lease_if_owned_or_absent(path, expected)
        except StorageMaintenanceError as cleanup:
            raise cleanup from original
    if isinstance(original, OSError):
        raise StorageMaintenanceError("MAINTENANCE_HOME_LIVENESS_INDETERMINATE", str(path)) from original
    raise original
def _remove_lease_if_owned(path: Path, token: str) -> None:
    inspection = _inspect_lease_path(path)
    if not inspection.exists:
        return
    if inspection.is_link_like or inspection.metadata is None or not stat.S_ISREG(inspection.metadata.st_mode):
        raise StorageMaintenanceError("MAINTENANCE_HOME_LIVENESS_INDETERMINATE", str(path))
    payload = _read_lease(path)
    if payload["token"] != token:
        raise StorageMaintenanceError("MAINTENANCE_HOME_LIVENESS_INDETERMINATE", str(path))
    current = _inspect_lease_path(path)
    if not current.exists:
        return
    if current.is_link_like or current.metadata is None or inspection.metadata is None or (current.metadata.st_dev, current.metadata.st_ino, current.metadata.st_size, current.metadata.st_mtime_ns) != (inspection.metadata.st_dev, inspection.metadata.st_ino, inspection.metadata.st_size, inspection.metadata.st_mtime_ns):
        raise StorageMaintenanceError("MAINTENANCE_HOME_LIVENESS_INDETERMINATE", str(path))
    try:
        path.unlink()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise StorageMaintenanceError("MAINTENANCE_HOME_LIVENESS_INDETERMINATE", str(path)) from exc
def _inspect_lease_path(path: Path) -> FinalPathInspection:
    try:
        return inspect_final_path(path)
    except OSError as exc:
        raise StorageMaintenanceError("MAINTENANCE_HOME_LIVENESS_INDETERMINATE", str(path)) from exc
def _read_lease(path: Path) -> dict[str, object]:
    try:
        inspection = _inspect_lease_path(path)
        if not inspection.exists or inspection.is_link_like or inspection.metadata is None or not stat.S_ISREG(inspection.metadata.st_mode):
            raise ValueError("unsafe lease")
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise StorageMaintenanceError("MAINTENANCE_HOME_LIVENESS_INDETERMINATE", str(path)) from exc
    return cast(dict[str, object], validate_home_lease(value, path=str(path)))
@contextmanager
def maintenance_guard(
    home: Path,
    *,
    owner_liveness: OwnerLiveness | None = None,
) -> Iterator[None]:
    """Serialize offline maintenance and reject live/unknown leases."""
    canonical = Path(home).resolve()
    descriptor = _acquire_guard(canonical)
    liveness = owner_liveness or ProcessOwnerLiveness()
    try:
        lease_root = _leases_dir(canonical)
        _ensure_safe_directory(lease_root)
        lease_root.mkdir(parents=True, exist_ok=True)
        for entry in tuple(lease_root.iterdir()):
            if entry.name.startswith("."):
                continue
            if entry.suffix != ".json":
                raise StorageMaintenanceError("MAINTENANCE_HOME_LIVENESS_INDETERMINATE")
            payload = _read_lease(entry)
            if payload["home_fingerprint"] != _home_fingerprint(canonical):
                raise StorageMaintenanceError("MAINTENANCE_HOME_LIVENESS_INDETERMINATE")
            pid = cast(int, payload["pid"])
            process_start_id = cast(str | None, payload["process_start_id"])
            status = liveness.check(pid, process_start_id)
            if status is OwnerStatus.ALIVE:
                raise StorageMaintenanceError("MAINTENANCE_HOME_ACTIVE")
            if status is OwnerStatus.INDETERMINATE:
                raise StorageMaintenanceError("MAINTENANCE_HOME_LIVENESS_INDETERMINATE")
            try:
                entry.unlink()
            except OSError as exc:
                raise StorageMaintenanceError("MAINTENANCE_HOME_LIVENESS_INDETERMINATE") from exc
        yield
    finally:
        _close_descriptor(descriptor)
__all__ = ["HomeLifecycleLease", "maintenance_guard"]

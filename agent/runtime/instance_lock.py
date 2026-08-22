"""Small cross-platform exclusive lock for one workspace state directory."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from agent.runtime.file_lock import OPEN_BINARY, try_lock_descriptor, unlock_descriptor
from agent.runtime.lock_filesystem import (
    UnsafeLockPathError,
    descriptor_matches_path,
    open_verified,
    sync_parent_directory,
    unlink_if_observed,
    unlink_if_same_stat,
    validate_final_entry,
)
from agent.runtime.lock_record import InvalidLockRecord, LockRecord, read_lock_record
from agent.runtime.process_identity import (
    OwnerLiveness,
    OwnerStatus,
    ProcessOwnerLiveness,
    current_process_start_id,
)

_MAX_LOCK_BYTES = 16_384
_MAX_RECOVERY_ATTEMPTS = 8


class InstanceLockError(RuntimeError):
    """Raised when another process or an unsafe lock state owns the state."""


@dataclass
class InstanceLock:
    path: Path
    token: str
    _acquired: bool = False
    _descriptor: int | None = None
    _guard_descriptor: int | None = None
    _record: LockRecord | None = None
    _owner_liveness: OwnerLiveness | None = None

    @classmethod
    def create(
        cls,
        path: str | Path,
        *,
        owner_liveness: OwnerLiveness | None = None,
    ) -> "InstanceLock":
        lexical = Path(path).expanduser()
        if not lexical.is_absolute():
            lexical = Path.cwd() / lexical
        return cls(
            lexical.parent.resolve() / lexical.name,
            uuid4().hex,
            _owner_liveness=(owner_liveness if owner_liveness is not None else ProcessOwnerLiveness()),
        )

    def acquire(self) -> None:
        if self._acquired:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._guard_descriptor = self._acquire_guard()
        try:
            for _ in range(_MAX_RECOVERY_ATTEMPTS):
                descriptor = self._try_publish_new_acquisition()
                if descriptor is not None:
                    return
                if self._inspect_existing_lock():
                    continue
            raise self._indeterminate_error("a recuperação do lock não convergiu")
        except Exception:
            self._release_guard()
            raise

    def release(self) -> None:
        if not self._acquired:
            return
        descriptor = self._descriptor
        record = self._record
        remove = False
        try:
            if descriptor is not None and record is not None:
                current = read_lock_record(descriptor, max_bytes=_MAX_LOCK_BYTES)
                if self._same_path_entry(descriptor) and current.raw == record.raw:
                    remove = current.token == self.token
        except (OSError, InvalidLockRecord):
            pass
        finally:
            if descriptor is not None:
                self._close_plain_descriptor(descriptor)
            self._descriptor = None
            self._record = None
            self._acquired = False
            if remove and record is not None:
                self._unlink_if_observed(record)
            self._release_guard()

    def __enter__(self) -> "InstanceLock":
        self.acquire()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        del exc_type, exc, traceback
        self.release()

    def _try_publish_new_acquisition(self) -> int | None:
        private_path = self.path.with_name(f".{self.path.name}.{self.token}.tmp")
        descriptor: int | None = None
        published = False
        try:
            validate_final_entry(self.path, allow_missing=True)
            descriptor = self._create_private_descriptor(private_path)
            self._write_payload(descriptor, self._new_payload())
            try:
                os.link(private_path, self.path)
            except FileExistsError:
                private_stat = os.fstat(descriptor)
                self._close_plain_descriptor(descriptor)
                unlink_if_same_stat(private_path, private_stat)
                return None
            published = True
            private_stat = os.fstat(descriptor)
            sync_parent_directory(self.path)
            self._close_plain_descriptor(descriptor)
            descriptor = None
            unlink_if_same_stat(private_path, private_stat)
            descriptor = open_verified(self.path, OPEN_BINARY | os.O_RDWR)
            if not os.path.samestat(os.fstat(descriptor), private_stat):
                raise self._indeterminate_error("o lock publicado mudou durante a aquisição")
            record = read_lock_record(descriptor, max_bytes=_MAX_LOCK_BYTES)
            if not descriptor_matches_path(self.path, descriptor):
                raise self._indeterminate_error("o lock publicado mudou durante a aquisição")
            self._descriptor = descriptor
            self._record = record
            self._acquired = True
            return descriptor
        except InstanceLockError:
            self._cleanup_publication(private_path, descriptor, published)
            raise
        except (OSError, UnsafeLockPathError, NotImplementedError) as exc:
            self._cleanup_publication(private_path, descriptor, published)
            raise self._indeterminate_error("não foi possível publicar o lock com segurança") from exc
        except Exception:
            self._cleanup_publication(private_path, descriptor, published)
            raise

    def _create_private_descriptor(self, path: Path) -> int:
        flags = OPEN_BINARY | os.O_CREAT | os.O_EXCL | os.O_RDWR
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOINHERIT", 0)
        try:
            return open_verified(path, flags)
        except (OSError, UnsafeLockPathError) as exc:
            raise self._indeterminate_error("não foi possível preparar o lock privado") from exc

    def _new_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "pid": os.getpid(),
            "token": self.token,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        process_identity = current_process_start_id()
        if process_identity is not None:
            payload["process_start_id"] = process_identity
        return payload

    def _cleanup_publication(self, private_path: Path, descriptor: int | None, published: bool) -> None:
        if descriptor is None:
            return
        try:
            descriptor_stat = os.fstat(descriptor)
        except OSError:
            descriptor_stat = None
        self._close_plain_descriptor(descriptor)
        if descriptor_stat is None:
            return
        if published:
            unlink_if_same_stat(self.path, descriptor_stat)
        unlink_if_same_stat(private_path, descriptor_stat)

    def _acquire_guard(self) -> int:
        guard_path = self.path.with_name(f"{self.path.name}.guard")
        exclusive_flags = OPEN_BINARY | os.O_CREAT | os.O_EXCL | os.O_RDWR
        exclusive_flags |= getattr(os, "O_CLOEXEC", 0)
        exclusive_flags |= getattr(os, "O_NOINHERIT", 0)
        existing_flags = OPEN_BINARY | os.O_RDWR
        existing_flags |= getattr(os, "O_CLOEXEC", 0)
        existing_flags |= getattr(os, "O_NOINHERIT", 0)
        descriptor: int | None = None
        try:
            try:
                descriptor = open_verified(guard_path, exclusive_flags)
            except FileExistsError:
                descriptor = open_verified(guard_path, existing_flags)
            if os.fstat(descriptor).st_size == 0:
                os.lseek(descriptor, 0, os.SEEK_SET)
                os.write(descriptor, b"\0")
                os.fsync(descriptor)
            if not descriptor_matches_path(guard_path, descriptor):
                raise self._indeterminate_error("o guard mudou durante a abertura")
            if not try_lock_descriptor(descriptor):
                raise self._active_error()
            if not descriptor_matches_path(guard_path, descriptor):
                raise self._indeterminate_error("o guard mudou durante a sincronização")
            return descriptor
        except InstanceLockError:
            if descriptor is not None:
                self._close_plain_descriptor(descriptor)
            raise
        except (OSError, UnsafeLockPathError) as exc:
            if descriptor is not None:
                self._close_plain_descriptor(descriptor)
            raise self._indeterminate_error("não foi possível sincronizar o guard") from exc

    def _inspect_existing_lock(self) -> bool:
        descriptor: int | None = None
        try:
            descriptor = open_verified(self.path, OPEN_BINARY | os.O_RDWR)
        except UnsafeLockPathError as exc:
            raise self._indeterminate_error("a entrada final do lock não é segura") from exc
        except OSError as exc:
            raise self._indeterminate_error("não foi possível inspecionar o lock") from exc
        try:
            try:
                observed = read_lock_record(descriptor, max_bytes=_MAX_LOCK_BYTES)
            except InvalidLockRecord as exc:
                raise self._indeterminate_error("o lock é inválido ou está incompleto") from exc
            liveness = self._owner_liveness or ProcessOwnerLiveness()
            status = liveness.check(observed.pid, observed.process_start_id)
            if status is OwnerStatus.ALIVE:
                raise self._active_error()
            if status is OwnerStatus.INDETERMINATE:
                raise self._indeterminate_error("a identidade do proprietário não pôde ser provada")
            if not self._same_record(descriptor, observed):
                return False
            self._close_plain_descriptor(descriptor)
            descriptor = None
            return self._unlink_if_observed(observed)
        finally:
            if descriptor is not None:
                self._close_plain_descriptor(descriptor)

    def _same_record(self, descriptor: int, observed: LockRecord) -> bool:
        if not self._same_path_entry(descriptor):
            return False
        try:
            current = read_lock_record(descriptor, max_bytes=_MAX_LOCK_BYTES)
        except (OSError, InvalidLockRecord):
            return False
        return current.raw == observed.raw and self._same_path_entry(descriptor)

    def _same_path_entry(self, descriptor: int) -> bool:
        return descriptor_matches_path(self.path, descriptor)

    def _write_payload(self, descriptor: int, payload: dict[str, Any]) -> None:
        raw = (json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        os.ftruncate(descriptor, 0)
        os.lseek(descriptor, 0, os.SEEK_SET)
        written = 0
        while written < len(raw):
            written += os.write(descriptor, raw[written:])
        os.fsync(descriptor)

    def _unlink_if_observed(self, observed: LockRecord) -> bool:
        return unlink_if_observed(self.path, observed.stat_result, observed.raw)

    @staticmethod
    def _close_plain_descriptor(descriptor: int) -> None:
        try:
            os.close(descriptor)
        except OSError:
            pass

    def _release_guard(self) -> None:
        descriptor = self._guard_descriptor
        self._guard_descriptor = None
        if descriptor is not None:
            unlock_descriptor(descriptor)
            self._close_plain_descriptor(descriptor)

    def _active_error(self) -> InstanceLockError:
        return InstanceLockError(f"O estado do workspace já está em uso: {self.path}")

    def _indeterminate_error(self, reason: str) -> InstanceLockError:
        return InstanceLockError(
            f"O estado do workspace não pôde ser validado com segurança ({reason}): {self.path}"
        )


__all__ = ["InstanceLock", "InstanceLockError"]

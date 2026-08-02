"""Cross-process writer lock for the persisted extension catalog."""

from __future__ import annotations

import errno
import logging
import os
from pathlib import Path
from typing import BinaryIO

from agent.tools.extension_catalog_errors import CatalogLockBusyError, CatalogLockError

_LOGGER = logging.getLogger(__name__)


class ExtensionCatalogLock:
    """A small advisory lock whose kernel ownership ends with the process."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._handle: BinaryIO | None = None

    def acquire(self) -> None:
        if self._handle is not None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise CatalogLockError(f"Falha ao criar diretorio do lock: {self.path.parent}") from exc
        try:
            handle = self.path.open("a+b")
            if self.path.stat().st_size == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            self._lock_handle(handle)
        except OSError as exc:
            if "handle" in locals():
                handle.close()
            if exc.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK, errno.EWOULDBLOCK):
                raise CatalogLockBusyError(f"Catalogo em uso: {self.path}") from exc
            raise CatalogLockError(f"Falha ao adquirir lock do catalogo: {self.path}") from exc
        self._handle = handle

    @staticmethod
    def _lock_handle(handle: BinaryIO) -> None:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)  # type: ignore[attr-defined]
            return
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)  # type: ignore[attr-defined]

    def release(self) -> None:
        handle = self._handle
        if handle is None:
            return
        self._handle = None
        release_error: CatalogLockError | None = None
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)  # type: ignore[attr-defined]
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)  # type: ignore[attr-defined]
        except OSError as exc:
            release_error = CatalogLockError(f"Falha ao liberar lock do catalogo: {self.path}")
            release_error.__cause__ = exc
        finally:
            try:
                handle.close()
            except OSError as exc:
                if release_error is None:
                    release_error = CatalogLockError(f"Falha ao fechar handle do lock: {self.path}")
                    release_error.__cause__ = exc
        if release_error is not None:
            raise release_error

    def __enter__(self) -> "ExtensionCatalogLock":
        self.acquire()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        del exc_type, traceback
        try:
            self.release()
        except CatalogLockError as release_error:
            if not isinstance(exc, BaseException):
                raise
            note = f"Falha ao liberar lock apos erro da operacao: {release_error}"
            add_note = getattr(exc, "add_note", None)
            if callable(add_note):
                add_note(note)
            else:  # pragma: no cover - Python 3.10 compatibility fallback.
                _LOGGER.warning(note, exc_info=release_error)


__all__ = ["ExtensionCatalogLock"]

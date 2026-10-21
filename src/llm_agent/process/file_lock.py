"""Cross-platform advisory descriptor locking used by runtime state locks."""

from __future__ import annotations

import errno
import os
from typing import Any

_LOCK_BUSY_ERRNOS = {errno.EACCES, errno.EAGAIN, errno.EBUSY}
OPEN_BINARY = getattr(os, "O_BINARY", 0)


def read_descriptor(descriptor: int, *, max_bytes: int) -> bytes:
    os.lseek(descriptor, 0, os.SEEK_SET)
    chunks: list[bytes] = []
    total = 0
    try:
        while total <= max_bytes:
            chunk = os.read(descriptor, min(4096, max_bytes + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
    finally:
        os.lseek(descriptor, 0, os.SEEK_SET)
    return b"".join(chunks)


def lock_descriptor(descriptor: int, *, blocking: bool) -> None:
    if os.name == "nt":
        import msvcrt

        msvcrt_module: Any = msvcrt
        os.lseek(descriptor, 0, os.SEEK_SET)
        mode = msvcrt_module.LK_LOCK if blocking else msvcrt_module.LK_NBLCK
        msvcrt_module.locking(descriptor, mode, 1)
        os.lseek(descriptor, 0, os.SEEK_SET)
        return
    import fcntl

    fcntl_module: Any = fcntl
    flags = fcntl_module.LOCK_EX if blocking else fcntl_module.LOCK_EX | fcntl_module.LOCK_NB
    fcntl_module.flock(descriptor, flags)


def try_lock_descriptor(descriptor: int) -> bool:
    try:
        lock_descriptor(descriptor, blocking=False)
        return True
    except OSError as exc:
        if isinstance(exc, PermissionError) or exc.errno in _LOCK_BUSY_ERRNOS:
            return False
        raise


def unlock_descriptor(descriptor: int) -> None:
    try:
        if os.name == "nt":
            import msvcrt

            msvcrt_module: Any = msvcrt
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt_module.locking(descriptor, msvcrt_module.LK_UNLCK, 1)
            os.lseek(descriptor, 0, os.SEEK_SET)
        else:
            import fcntl

            fcntl_module: Any = fcntl
            fcntl_module.flock(descriptor, fcntl_module.LOCK_UN)
    except OSError:
        pass


__all__ = [
    "OPEN_BINARY",
    "lock_descriptor",
    "read_descriptor",
    "try_lock_descriptor",
    "unlock_descriptor",
]

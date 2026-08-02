"""Small cross-platform exclusive lock for one workspace state directory."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


class InstanceLockError(RuntimeError):
    """Raised when another process or application owns the state."""


@dataclass
class InstanceLock:
    path: Path
    token: str
    _acquired: bool = False

    @classmethod
    def create(cls, path: str | Path) -> "InstanceLock":
        return cls(Path(path).expanduser().resolve(), uuid4().hex)

    def acquire(self) -> None:
        if self._acquired:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        try:
            descriptor = os.open(self.path, flags)
        except FileExistsError as exc:
            raise InstanceLockError(
                f"O estado do workspace já está em uso: {self.path}"
            ) from exc
        payload = {
            "pid": os.getpid(),
            "token": self.token,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(payload, handle, ensure_ascii=False)
                handle.write("\n")
            self._acquired = True
        except Exception:
            self.path.unlink(missing_ok=True)
            raise

    def release(self) -> None:
        if not self._acquired:
            return
        try:
            current = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            current = {}
        if current.get("token") == self.token:
            self.path.unlink(missing_ok=True)
        self._acquired = False

    def __enter__(self) -> "InstanceLock":
        self.acquire()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        del exc_type, exc, traceback
        self.release()


__all__ = ["InstanceLock", "InstanceLockError"]

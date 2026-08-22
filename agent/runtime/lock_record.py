"""Validated metadata model for the workspace application lock."""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from datetime import datetime

from agent.runtime.file_lock import read_descriptor


class InvalidLockRecord(ValueError):
    """Internal marker for malformed or incomplete lock metadata."""


@dataclass(frozen=True)
class LockRecord:
    pid: int
    token: str
    created_at: str
    process_start_id: str | None
    raw: bytes
    stat_result: os.stat_result


def read_lock_record(descriptor: int, *, max_bytes: int) -> LockRecord:
    stat_result = os.fstat(descriptor)
    if not stat.S_ISREG(stat_result.st_mode) or stat_result.st_size > max_bytes:
        raise InvalidLockRecord("lock não é um arquivo regular limitado")
    raw = read_descriptor(descriptor, max_bytes=max_bytes)
    if len(raw) > max_bytes:
        raise InvalidLockRecord("lock excede o limite de bytes")
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InvalidLockRecord("JSON de lock inválido") from exc
    if not isinstance(document, dict):
        raise InvalidLockRecord("JSON de lock não é um objeto")
    pid = document.get("pid")
    token = document.get("token")
    created_at = document.get("created_at")
    process_identity = document.get("process_start_id")
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        raise InvalidLockRecord("pid ausente ou inválido")
    if not isinstance(token, str) or not token:
        raise InvalidLockRecord("token ausente ou inválido")
    if not isinstance(created_at, str) or not created_at:
        raise InvalidLockRecord("created_at ausente ou inválido")
    try:
        datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise InvalidLockRecord("created_at inválido") from exc
    if process_identity is not None and (
        not isinstance(process_identity, str) or not process_identity
    ):
        raise InvalidLockRecord("process_start_id inválido")
    return LockRecord(
        pid=pid,
        token=token,
        created_at=created_at,
        process_start_id=process_identity,
        raw=raw,
        stat_result=stat_result,
    )


__all__ = ["InvalidLockRecord", "LockRecord", "read_lock_record"]

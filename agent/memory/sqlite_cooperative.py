"""Cooperative SQLite execution for task-owned memory operations."""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable
from contextlib import closing
from typing import Any, TypeVar

from agent.cancellation import is_cancellation_requested

_BUSY_RETRY_SECONDS = 0.01
_DIRECT_OPERATION_TIMEOUT_SECONDS = 5.0
_ResultT = TypeVar("_ResultT")


class SqliteOperationCancelled(RuntimeError):
    """The transaction stopped before its canonical commit point."""


def ensure_not_cancelled(token: Any | None, event: Any | None) -> None:
    if is_cancellation_requested(token, event):
        raise SqliteOperationCancelled("memory transaction cancelled before commit")


def run_sqlite_operation(
    database: str,
    operation: Callable[[sqlite3.Connection], _ResultT],
    *,
    cancellation_token: Any | None = None,
    cancellation_event: Any | None = None,
    connect: Callable[..., sqlite3.Connection] = sqlite3.connect,
) -> _ResultT:
    """Retry SQLite lock contention while remaining cooperatively cancellable."""

    deadline = time.monotonic() + _DIRECT_OPERATION_TIMEOUT_SECONDS
    while True:
        ensure_not_cancelled(cancellation_token, cancellation_event)
        try:
            with closing(connect(database, timeout=0.0)) as connection:
                connection.set_progress_handler(
                    lambda: int(
                        is_cancellation_requested(
                            cancellation_token,
                            cancellation_event,
                        )
                    ),
                    100,
                )
                return operation(connection)
        except sqlite3.OperationalError as exc:
            if is_cancellation_requested(cancellation_token, cancellation_event):
                raise SqliteOperationCancelled(
                    "memory transaction cancelled before commit"
                ) from exc
            if not _is_busy(exc) or time.monotonic() >= deadline:
                raise
            _wait_for_retry(cancellation_event)


def _is_busy(exc: sqlite3.OperationalError) -> bool:
    message = str(exc).casefold()
    return "locked" in message or "busy" in message


def _wait_for_retry(event: Any | None) -> None:
    wait = getattr(event, "wait", None)
    if callable(wait):
        wait(_BUSY_RETRY_SECONDS)
    else:
        time.sleep(_BUSY_RETRY_SECONDS)


__all__ = [
    "SqliteOperationCancelled",
    "ensure_not_cancelled",
    "run_sqlite_operation",
]

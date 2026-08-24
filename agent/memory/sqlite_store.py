"""SQLite-backed portion of :class:`agent.memory.memory.AgentMemory`."""

from __future__ import annotations

import json
import os
import sqlite3
from typing import Any

from agent.memory.path_safety import LinkLikePathError, reject_link_like
from agent.memory.sqlite_cooperative import (
    SqliteOperationCancelled,
    ensure_not_cancelled,
    run_sqlite_operation,
)
from agent.runtime.logging import logger


class MemoryDatabaseError(RuntimeError):
    """A durable SQLite memory operation failed."""


class MemoryOperationCancelled(RuntimeError):
    """A memory transaction was cancelled before its commit point."""


class SqliteMemoryStoreMixin:
    """Own initialization and fail-atomic SQLite memory transactions."""

    db_path: str
    _initialized: bool
    state: dict[str, Any]

    def initialize(
        self,
        *,
        cancellation_token: Any | None = None,
        cancellation_event: Any | None = None,
    ) -> None:
        if self._initialized:
            return
        self._ensure_db(cancellation_token, cancellation_event)
        findings, summaries = self._load_db_state(
            cancellation_token,
            cancellation_event,
        )
        self.state["key_findings"] = findings
        self.state["file_summaries"] = summaries
        self._initialized = True

    def _ensure_db(
        self,
        cancellation_token: Any | None = None,
        cancellation_event: Any | None = None,
    ) -> None:
        ensure_not_cancelled(cancellation_token, cancellation_event)
        self._reject_linked_database()
        try:
            os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        except OSError as exc:
            raise MemoryDatabaseError(
                f"Falha ao criar diretório da memória SQLite: {exc}"
            ) from exc
        self._write_database(
            "inicializar a memória",
            (
                (
                    "CREATE TABLE IF NOT EXISTS key_findings "
                    "(key TEXT PRIMARY KEY, value TEXT)",
                    (),
                ),
                (
                    "CREATE TABLE IF NOT EXISTS file_summaries "
                    "(file_path TEXT PRIMARY KEY, summary TEXT)",
                    (),
                ),
            ),
            cancellation_token=cancellation_token,
            cancellation_event=cancellation_event,
        )

    def _load_db_state(
        self,
        cancellation_token: Any | None = None,
        cancellation_event: Any | None = None,
    ) -> tuple[dict[str, Any], dict[str, str]]:
        findings: dict[str, Any] = {}
        try:
            self._reject_linked_database()

            def load_rows(conn: sqlite3.Connection) -> tuple[list[Any], list[Any]]:
                return (
                    conn.execute("SELECT key, value FROM key_findings").fetchall(),
                    conn.execute(
                        "SELECT file_path, summary FROM file_summaries"
                    ).fetchall(),
                )

            finding_rows, summary_rows = run_sqlite_operation(
                self.db_path,
                load_rows,
                cancellation_token=cancellation_token,
                cancellation_event=cancellation_event,
                connect=sqlite3.connect,
            )
            for key, value in finding_rows:
                try:
                    findings[key] = json.loads(value)
                except Exception:
                    findings[key] = value
            return findings, dict(summary_rows)
        except SqliteOperationCancelled as exc:
            raise MemoryOperationCancelled(str(exc)) from exc
        except Exception as exc:
            logger.warning("Falha ao carregar estado da memória SQLite: %s", exc)
            raise MemoryDatabaseError(
                f"Falha ao carregar a memória do SQLite: {exc}"
            ) from exc

    def _write_database(
        self,
        operation: str,
        statements: tuple[tuple[str, tuple[Any, ...]], ...],
        *,
        cancellation_token: Any | None = None,
        cancellation_event: Any | None = None,
    ) -> None:
        try:
            self._reject_linked_database()

            def write(conn: sqlite3.Connection) -> None:
                try:
                    for statement, parameters in statements:
                        ensure_not_cancelled(
                            cancellation_token,
                            cancellation_event,
                        )
                        conn.execute(statement, parameters)
                    ensure_not_cancelled(cancellation_token, cancellation_event)
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise

            run_sqlite_operation(
                self.db_path,
                write,
                cancellation_token=cancellation_token,
                cancellation_event=cancellation_event,
                connect=sqlite3.connect,
            )
        except SqliteOperationCancelled as exc:
            raise MemoryOperationCancelled(str(exc)) from exc
        except Exception as exc:
            logger.warning("Falha ao %s em SQLite: %s", operation, exc)
            raise MemoryDatabaseError(
                f"Falha ao {operation} no SQLite: {exc}"
            ) from exc

    def _reject_linked_database(self) -> None:
        try:
            reject_link_like(self.db_path)
        except (LinkLikePathError, OSError) as exc:
            raise MemoryDatabaseError(
                f"Arquivo SQLite de memória inseguro: {exc}"
            ) from exc


__all__ = [
    "MemoryDatabaseError",
    "MemoryOperationCancelled",
    "SqliteMemoryStoreMixin",
]

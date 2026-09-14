"""Model-free, task-free read-only workspace query plane."""

from __future__ import annotations

import os
import shutil  # noqa: F401
import subprocess  # noqa: F401
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Event
from typing import Any

from agent.interfaces.cli.query_find import find_candidates, find_matches
from agent.runtime.path_safety import WorkspacePathError, resolve_workspace_path
from agent.runtime.workspace_context import WorkspaceContext

MAX_QUERY_OUTPUT_CHARS = 24000
MAX_LIST_ITEMS = 500
MAX_LIST_SCAN_ENTRIES = 4_000
MAX_FIND_MATCHES = 200
MAX_FIND_FILES = 2000
MAX_READ_BYTES = 128 * 1024
MAX_FIND_FILE_BYTES = 128 * 1024
MAX_GIT_OUTPUT_BYTES = 256 * 1024
MAX_DIFF_FILES = 200
QUERY_TIMEOUT_SECONDS = 20
MAX_FIND_PATTERN_CHARS = 512
MAX_FIND_SCAN_ENTRIES = MAX_FIND_FILES
MAX_FIND_SCAN_CHARS = 8_192
_FIND_LITERAL_META = frozenset("\\.^$*+?{}[]()|")


@dataclass(frozen=True)
class QueryRequest:
    query_generation: int
    workspace_id: str
    workspace_generation: int
    command_id: str
    arguments: dict[str, Any]
    task_active: bool


@dataclass(frozen=True)
class QueryResult:
    query_generation: int
    workspace_id: str
    workspace_generation: int
    command_id: str
    ok: bool
    data: Any = None
    error: str | None = None
    truncated: bool = False
    live_marker: str | None = None


def _marker(task_active: bool) -> str | None:
    if not task_active:
        return None
    now = datetime.now(timezone.utc).isoformat()
    return f"LIVE SNAPSHOT | task active | {now}"


def _result(request: QueryRequest, *, ok: bool, data: Any = None, error: str | None = None, truncated: bool = False) -> QueryResult:
    return QueryResult(
        request.query_generation,
        request.workspace_id,
        request.workspace_generation,
        request.command_id,
        ok,
        data=data,
        error=error,
        truncated=truncated,
        live_marker=_marker(request.task_active),
    )


from agent.interfaces.cli.query_git import GitQueryMixin  # noqa: E402


class ReadOnlyWorkspaceQueryService(GitQueryMixin):
    """Pure workspace observations with no application/task runtime access."""

    def __init__(self, workspace: WorkspaceContext | str | Path) -> None:
        self.workspace = workspace if isinstance(workspace, WorkspaceContext) else WorkspaceContext.create(workspace)

    def _path(self, value: str | Path, *, require_file: bool = False, require_directory: bool = False) -> Path:
        raw = str(value)
        if not raw or "\x00" in raw:
            raise WorkspacePathError("caminho vazio ou inválido")
        return resolve_workspace_path(
            self.workspace.root,
            raw,
            require_file=require_file,
            require_directory=require_directory,
        )

    def list_files(self, request: QueryRequest, cancel: Event) -> QueryResult:
        raw_path = str(request.arguments.get("path", "."))
        try:
            selected = self._path(raw_path, require_directory=True)
            rows: list[dict[str, str]] = []
            truncated = False
            scanned_entries = 0
            with os.scandir(selected) as entries:
                for entry in entries:
                    if cancel.is_set():
                        return _result(request, ok=False, error="query ls: cancelado")
                    scanned_entries += 1
                    if scanned_entries > MAX_LIST_SCAN_ENTRIES:
                        truncated = True
                        break
                    try:
                        relative = (selected / entry.name).relative_to(self.workspace.root).as_posix()
                        safe = self._path(relative)
                    except (WorkspacePathError, FileNotFoundError, OSError):
                        # Link-like descendants are never followed by the
                        # query plane, even when the directory listing itself
                        # contains them.
                        continue
                    rows.append(
                        {
                            "name": entry.name,
                            "relative": relative,
                            "type": "dir" if safe.is_dir() else "file",
                        }
                    )
                    rows.sort(key=lambda row: (row["name"].casefold(), row["name"]))
                    if len(rows) > MAX_LIST_ITEMS:
                        rows.pop()
                        truncated = True
            return _result(request, ok=True, data={"items": rows, "truncated": truncated}, truncated=truncated)
        except (WorkspacePathError, FileNotFoundError, NotADirectoryError, PermissionError, OSError) as exc:
            return _result(request, ok=False, error=f"query ls: {exc}")

    def read_file(self, request: QueryRequest, cancel: Event) -> QueryResult:
        raw_path = str(request.arguments.get("file_path", ""))
        try:
            selected = self._path(raw_path, require_file=True)
            allowed = {
                ".txt", ".md", ".py", ".json", ".csv", ".log", ".yaml", ".yml", ".html", ".css", ".js", ".ts", ".tsx", ".toml", ".ini", ".cfg", ".xml", ".rst",
            }
            if selected.suffix.lower() not in allowed and selected.name.casefold() not in {"readme", "license", "makefile", "dockerfile"}:
                return _result(request, ok=False, error=f"query read: tipo de arquivo não permitido: {selected.name}")
            with selected.open("rb") as handle:
                payload = handle.read(MAX_READ_BYTES + 1)
            truncated = len(payload) > MAX_READ_BYTES
            raw = payload[:MAX_READ_BYTES]
            text = raw.decode("utf-8")
            lines = text.splitlines(keepends=True)
            start = request.arguments.get("start_line")
            end = request.arguments.get("end_line")
            if start is not None or end is not None:
                start_number = max(1, int(start or 1))
                end_number = max(start_number, int(end or len(lines)))
                text = "".join(lines[start_number - 1 : end_number])
            char_truncated = len(text) > MAX_QUERY_OUTPUT_CHARS
            text = text[:MAX_QUERY_OUTPUT_CHARS]
            return _result(request, ok=True, data={"path": raw_path, "content": text, "truncated": truncated or char_truncated}, truncated=truncated or char_truncated)
        except UnicodeDecodeError:
            return _result(request, ok=False, error="query read: arquivo não é UTF-8")
        except (WorkspacePathError, FileNotFoundError, IsADirectoryError, PermissionError, OSError, ValueError) as exc:
            return _result(request, ok=False, error=f"query read: {exc}")

    def _find_candidates(self, selected: Path, cancel: Event) -> tuple[list[Path], bool] | None:
        return find_candidates(
            selected,
            cancel,
            max_files=MAX_FIND_FILES,
            max_scan_entries=MAX_FIND_SCAN_ENTRIES,
        )

    def _find_matches(
        self,
        candidates: list[Path],
        needle: str,
        *,
        case_sensitive: bool,
        cancel: Event,
    ) -> tuple[list[dict[str, Any]], bool, bool]:
        return find_matches(
            candidates,
            needle,
            case_sensitive=case_sensitive,
            cancel=cancel,
            workspace_root=self.workspace.root,
            resolve_path=lambda relative: self._path(relative, require_file=True),
            max_files=MAX_FIND_FILES,
            max_matches=MAX_FIND_MATCHES,
            max_file_bytes=MAX_FIND_FILE_BYTES,
            max_scan_chars=MAX_FIND_SCAN_CHARS,
        )

    def find(self, request: QueryRequest, cancel: Event) -> QueryResult:
        pattern = str(request.arguments.get("pattern", ""))
        if not pattern:
            return _result(request, ok=False, error="query find: padrão vazio")
        try:
            if any(token in _FIND_LITERAL_META for token in pattern):
                raise ValueError("interactive /find accepts literal text only; regex syntax is unsupported")
            if len(pattern) > MAX_FIND_PATTERN_CHARS:
                raise ValueError(f"padrão excede {MAX_FIND_PATTERN_CHARS} caracteres")
            # The interactive query grammar is deliberately literal-only.
        except ValueError as exc:
            return _result(request, ok=False, error=f"query find: pattern invalid: {exc}")
        raw_path = str(request.arguments.get("path", "."))
        try:
            selected = self._path(raw_path)
            if not selected.exists():
                return _result(request, ok=False, error=f"query find: caminho não encontrado: {raw_path}")
            candidate_result = self._find_candidates(selected, cancel)
            if candidate_result is None:
                return _result(request, ok=False, error="query find: cancelado")
            candidates, candidate_truncated = candidate_result
            matches, truncated, cancelled = self._find_matches(
                candidates,
                pattern,
                case_sensitive=bool(request.arguments.get("case_sensitive", True)),
                cancel=cancel,
            )
            if cancelled:
                return _result(request, ok=False, error="query find: cancelado")
            truncated = truncated or candidate_truncated
            return _result(request, ok=True, data={"matches": matches, "truncated": truncated}, truncated=truncated)
        except (WorkspacePathError, FileNotFoundError, PermissionError, OSError) as exc:
            return _result(request, ok=False, error=f"query find: {exc}")

    def execute(self, request: QueryRequest, cancel: Event) -> QueryResult:
        handlers = {
            "list_files": self.list_files,
            "read": self.read_file,
            "find": self.find,
            "git_status": self.git_status,
            "diff": self.diff,
        }
        handler = handlers.get(request.command_id)
        if handler is None:
            return _result(request, ok=False, error=f"query desconhecida: {request.command_id}")
        return handler(request, cancel)


from agent.interfaces.cli.query_executor import BoundedQueryExecutor  # noqa: E402

__all__ = [
    "BoundedQueryExecutor",
    "MAX_QUERY_OUTPUT_CHARS",
    "QueryRequest",
    "QueryResult",
    "ReadOnlyWorkspaceQueryService",
]

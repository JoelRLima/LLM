"""Canonical UI-neutral workspace query contracts and list/read semantics."""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Protocol, cast

from agent.application_services.query_git import run_git
from agent.runtime.path_safety import WorkspacePathError, resolve_workspace_path
from agent.runtime.workspace_context import WorkspaceContext

WORKSPACE_QUERY_CONTRACT_VERSION = 1
QUERY_INVALID_REQUEST = "QUERY_INVALID_REQUEST"
QUERY_PATH_INVALID = "QUERY_PATH_INVALID"
QUERY_PATH_NOT_FOUND = "QUERY_PATH_NOT_FOUND"
QUERY_PATH_TYPE_INVALID = "QUERY_PATH_TYPE_INVALID"
QUERY_PERMISSION_DENIED = "QUERY_PERMISSION_DENIED"
QUERY_FILE_TYPE_UNSUPPORTED = "QUERY_FILE_TYPE_UNSUPPORTED"
QUERY_FILE_NOT_UTF8 = "QUERY_FILE_NOT_UTF8"
QUERY_FIND_PATTERN_EMPTY = "QUERY_FIND_PATTERN_EMPTY"
QUERY_FIND_PATTERN_INVALID = "QUERY_FIND_PATTERN_INVALID"
QUERY_GIT_UNAVAILABLE = "QUERY_GIT_UNAVAILABLE"
QUERY_GIT_FAILED = "QUERY_GIT_FAILED"
QUERY_TIMEOUT = "QUERY_TIMEOUT"
QUERY_CANCELLED = "QUERY_CANCELLED"
QUERY_IO_FAILED = "QUERY_IO_FAILED"

MAX_DIFF_FILES = 200
MAX_FIND_MATCHES, MAX_FIND_FILES, MAX_FIND_FILE_BYTES = 200, 2_000, 128 * 1024
MAX_FIND_PATTERN_CHARS, MAX_FIND_SCAN_ENTRIES, MAX_FIND_SCAN_CHARS = 512, MAX_FIND_FILES, 8_192
MAX_GIT_OUTPUT_BYTES, MAX_LIST_ITEMS, MAX_LIST_SCAN_ENTRIES = 256 * 1024, 500, 4_000
MAX_QUERY_OUTPUT_CHARS, MAX_READ_BYTES, QUERY_TIMEOUT_SECONDS = 24_000, 128 * 1024, 20

_READ_SUFFIXES = frozenset({".txt", ".md", ".py", ".json", ".csv", ".log", ".yaml", ".yml", ".html", ".css", ".js", ".ts", ".tsx", ".toml", ".ini", ".cfg", ".xml", ".rst"})
_READ_BASENAMES = frozenset({"readme", "license", "makefile", "dockerfile"})


class WorkspaceQueryKind(str, Enum):
    LIST_FILES = "list_files"
    READ = "read"
    FIND = "find"
    GIT_STATUS = "git_status"
    DIFF = "diff"


class WorkspaceQueryStatus(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class QueryCancellation(Protocol):
    def is_cancelled(self) -> bool: ...


def _freeze(value: object) -> object:
    return (MappingProxyType({key: _freeze(item) for key, item in value.items()}) if isinstance(value, Mapping) else
            tuple(_freeze(item) for item in value) if isinstance(value, (list, tuple)) else
            frozenset(_freeze(item) for item in value) if isinstance(value, set) else value)


@dataclass(frozen=True, slots=True)
class WorkspaceQueryRequest:
    kind: WorkspaceQueryKind
    arguments: Mapping[str, object]

    def __post_init__(self) -> None:
        if not isinstance(self.kind, WorkspaceQueryKind):
            raise TypeError("kind must be WorkspaceQueryKind")
        if not isinstance(self.arguments, Mapping):
            raise TypeError("arguments must be a mapping")
        frozen = _freeze(dict(self.arguments))
        if not isinstance(frozen, Mapping):
            raise TypeError("arguments must be a mapping")
        object.__setattr__(self, "arguments", frozen)


@dataclass(frozen=True, slots=True)
class WorkspaceQueryResult:
    kind: WorkspaceQueryKind
    status: WorkspaceQueryStatus
    data: object | None = None
    reason_code: str | None = None
    error: str | None = None
    truncated: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.kind, WorkspaceQueryKind):
            raise TypeError("kind must be WorkspaceQueryKind")
        if not isinstance(self.status, WorkspaceQueryStatus):
            raise TypeError("status must be WorkspaceQueryStatus")
        if not isinstance(self.truncated, bool):
            raise TypeError("truncated must be bool")
        if self.status is WorkspaceQueryStatus.SUCCEEDED and (self.reason_code is not None or self.error is not None):
            raise ValueError("successful query cannot carry failure metadata")
        if self.status is WorkspaceQueryStatus.FAILED and (not isinstance(self.reason_code, str) or not self.reason_code):
            raise ValueError("failed query requires a reason code")
        if self.status is WorkspaceQueryStatus.CANCELLED and self.reason_code != QUERY_CANCELLED:
            raise ValueError("cancelled query requires QUERY_CANCELLED")


@dataclass(frozen=True, slots=True)
class _ResolvedPathError(ValueError):
    reason_code: str
    message: str


def _cancelled(request: WorkspaceQueryRequest) -> WorkspaceQueryResult:
    return WorkspaceQueryResult(request.kind, WorkspaceQueryStatus.CANCELLED, reason_code=QUERY_CANCELLED)


def _failed(request: WorkspaceQueryRequest, reason_code: str, error: str) -> WorkspaceQueryResult:
    return WorkspaceQueryResult(request.kind, WorkspaceQueryStatus.FAILED, reason_code=reason_code, error=error[:2_000])


def _succeeded(request: WorkspaceQueryRequest, data: object, *, truncated: bool = False) -> WorkspaceQueryResult:
    return WorkspaceQueryResult(request.kind, WorkspaceQueryStatus.SUCCEEDED, data=data, truncated=truncated)


def _cancel_requested(cancellation: QueryCancellation) -> bool:
    is_cancelled = getattr(cancellation, "is_cancelled", None)
    if not callable(is_cancelled):
        raise TypeError("cancellation must implement is_cancelled()")
    return bool(is_cancelled())


def _error_text(prefix: str, exc: BaseException) -> str:
    return f"{prefix}: {str(exc).strip() or type(exc).__name__}"[:2_000]


class ReadOnlyWorkspaceQueryService:
    def __init__(self, workspace: WorkspaceContext | str | Path) -> None:
        self.workspace = workspace if isinstance(workspace, WorkspaceContext) else WorkspaceContext.create(workspace)

    def _resolve(self, value: str, *, require_file: bool = False, require_directory: bool = False) -> Path:
        if not isinstance(value, str) or not value or "\x00" in value:
            raise _ResolvedPathError(QUERY_PATH_INVALID, "path is empty or contains NUL")
        try:
            selected = resolve_workspace_path(self.workspace.root, value)
        except WorkspacePathError as exc:
            raise _ResolvedPathError(QUERY_PATH_INVALID, str(exc)) from exc
        except FileNotFoundError as exc:
            raise _ResolvedPathError(QUERY_PATH_NOT_FOUND, str(exc)) from exc
        except (NotADirectoryError, IsADirectoryError) as exc:
            raise _ResolvedPathError(QUERY_PATH_TYPE_INVALID, str(exc)) from exc
        except PermissionError as exc:
            raise _ResolvedPathError(QUERY_PERMISSION_DENIED, str(exc)) from exc
        except (OSError, ValueError, RuntimeError) as exc:
            raise _ResolvedPathError(QUERY_PATH_INVALID, str(exc)) from exc
        if not selected.exists():
            raise _ResolvedPathError(QUERY_PATH_NOT_FOUND, value)
        if require_file and not selected.is_file():
            raise _ResolvedPathError(QUERY_PATH_TYPE_INVALID, value)
        if require_directory and not selected.is_dir():
            raise _ResolvedPathError(QUERY_PATH_TYPE_INVALID, value)
        return selected

    def _arguments(self, request: WorkspaceQueryRequest, kind: WorkspaceQueryKind) -> dict[str, object] | WorkspaceQueryResult:
        if request.kind is not kind:
            return _failed(request, QUERY_INVALID_REQUEST, "request kind does not match handler")
        arguments = request.arguments
        allowed = {WorkspaceQueryKind.LIST_FILES: frozenset({"path"}), WorkspaceQueryKind.READ: frozenset({"file_path", "start_line", "end_line"}), WorkspaceQueryKind.FIND: frozenset({"pattern", "path", "case_sensitive"}), WorkspaceQueryKind.GIT_STATUS: frozenset(), WorkspaceQueryKind.DIFF: frozenset({"paths", "detail"})}[kind]
        if any(not isinstance(key, str) or key not in allowed for key in arguments):
            return _failed(request, QUERY_INVALID_REQUEST, "unknown query argument")
        if kind is WorkspaceQueryKind.GIT_STATUS:
            return {} if not arguments else _failed(request, QUERY_INVALID_REQUEST, "git_status does not accept arguments")
        return {WorkspaceQueryKind.LIST_FILES: self._list_arguments, WorkspaceQueryKind.READ: self._read_arguments, WorkspaceQueryKind.FIND: self._find_arguments, WorkspaceQueryKind.DIFF: self._diff_arguments}[kind](request, arguments)

    @staticmethod
    def _list_arguments(request: WorkspaceQueryRequest, arguments: Mapping[str, object]) -> dict[str, object] | WorkspaceQueryResult:
        return {"path": path} if isinstance((path := arguments.get("path", ".")), str) else _failed(request, QUERY_INVALID_REQUEST, "path must be a string")

    _valid_line = staticmethod(lambda value: value is None or (isinstance(value, int) and not isinstance(value, bool) and value >= 1))

    @classmethod
    def _read_arguments(cls, request: WorkspaceQueryRequest, arguments: Mapping[str, object]) -> dict[str, object] | WorkspaceQueryResult:
        path, start, end = arguments.get("file_path"), arguments.get("start_line"), arguments.get("end_line")
        return _failed(request, QUERY_INVALID_REQUEST, "file_path must be a non-empty string") if not isinstance(path, str) or not path else _failed(request, QUERY_INVALID_REQUEST, "line bounds must be integers >= 1") if not cls._valid_line(start) or not cls._valid_line(end) else _failed(request, QUERY_INVALID_REQUEST, "end_line must be >= start_line") if start is not None and end is not None and cast(int, end) < cast(int, start) else {"file_path": path, "start_line": start, "end_line": end}

    @staticmethod
    def _find_arguments(request: WorkspaceQueryRequest, arguments: Mapping[str, object]) -> dict[str, object] | WorkspaceQueryResult:
        pattern, path, case_sensitive = arguments.get("pattern"), arguments.get("path", "."), arguments.get("case_sensitive", True)
        return _failed(request, QUERY_INVALID_REQUEST, "pattern must be a string") if not isinstance(pattern, str) else _failed(request, QUERY_INVALID_REQUEST, "path must be a string") if not isinstance(path, str) else _failed(request, QUERY_INVALID_REQUEST, "case_sensitive must be bool") if not isinstance(case_sensitive, bool) else {"pattern": pattern, "path": path, "case_sensitive": case_sensitive}

    @staticmethod
    def _diff_arguments(request: WorkspaceQueryRequest, arguments: Mapping[str, object]) -> dict[str, object] | WorkspaceQueryResult:
        paths, detail = arguments.get("paths", ()), arguments.get("detail", False)
        return _failed(request, QUERY_INVALID_REQUEST, "paths must be a sequence of strings") if isinstance(paths, (str, bytes, bytearray)) or not isinstance(paths, Sequence) or not all(isinstance(path, str) for path in paths) else {"paths": tuple(paths), "detail": detail} if isinstance(detail, bool) else _failed(request, QUERY_INVALID_REQUEST, "detail must be bool")

    def _scan_list(self, selected: Path, cancellation: QueryCancellation) -> tuple[list[dict[str, str]], bool, bool]:
        rows: list[dict[str, str]] = []
        scanned_entries, truncated = 0, False
        with os.scandir(selected) as entries:
            for scanned_entries, entry in enumerate(entries, 1):
                if _cancel_requested(cancellation):
                    return rows, truncated, True
                if scanned_entries > MAX_LIST_SCAN_ENTRIES:
                    return rows, True, False
                relative = (selected / entry.name).relative_to(self.workspace.root).as_posix()
                try:
                    safe = self._resolve(relative)
                except Exception:
                    continue
                rows.append({"name": entry.name, "relative": relative, "type": "dir" if safe.is_dir() else "file"})
                rows.sort(key=lambda row: (row["name"].casefold(), row["name"]))
                if len(rows) > MAX_LIST_ITEMS:
                    rows.pop()
                    truncated = True
        truncated = truncated or len(rows) >= MAX_LIST_ITEMS and scanned_entries > len(rows)
        return rows, truncated, False

    def list_files(self, request: WorkspaceQueryRequest, cancellation: QueryCancellation) -> WorkspaceQueryResult:
        arguments = self._arguments(request, WorkspaceQueryKind.LIST_FILES)
        if isinstance(arguments, WorkspaceQueryResult):
            return arguments
        if _cancel_requested(cancellation):
            return _cancelled(request)
        try:
            selected = self._resolve(cast(str, arguments["path"]), require_directory=True)
            rows, truncated, cancelled = self._scan_list(selected, cancellation)
            if cancelled:
                return _cancelled(request)
            return _succeeded(request, {"items": rows, "truncated": truncated}, truncated=truncated)
        except _ResolvedPathError as exc:
            return _failed(request, exc.reason_code, _error_text("query list_files", ValueError(exc.message)))
        except PermissionError as exc:
            return _failed(request, QUERY_PERMISSION_DENIED, _error_text("query list_files", exc))
        except (OSError, ValueError) as exc:
            return _failed(request, QUERY_IO_FAILED, _error_text("query list_files", exc))

    def read_file(self, request: WorkspaceQueryRequest, cancellation: QueryCancellation) -> WorkspaceQueryResult:
        arguments = self._arguments(request, WorkspaceQueryKind.READ)
        if isinstance(arguments, WorkspaceQueryResult):
            return arguments
        if _cancel_requested(cancellation):
            return _cancelled(request)
        raw_path = cast(str, arguments["file_path"])
        try:
            selected = self._resolve(raw_path, require_file=True)
            if selected.suffix.lower() not in _READ_SUFFIXES and selected.name.casefold() not in _READ_BASENAMES:
                return _failed(request, QUERY_FILE_TYPE_UNSUPPORTED, f"query read: unsupported file type: {selected.name}")
            with selected.open("rb") as handle:
                payload = handle.read(MAX_READ_BYTES + 1)
            if _cancel_requested(cancellation):
                return _cancelled(request)
            byte_truncated = len(payload) > MAX_READ_BYTES
            text = payload[:MAX_READ_BYTES].decode("utf-8")
            lines = text.splitlines(keepends=True)
            start = cast(int | None, arguments["start_line"])
            end = cast(int | None, arguments["end_line"])
            if start is not None or end is not None:
                text = "".join(lines[(start or 1) - 1 : end or len(lines)])
            truncated = byte_truncated or len(text) > MAX_QUERY_OUTPUT_CHARS
            text = text[:MAX_QUERY_OUTPUT_CHARS]
            return _succeeded(request, {"path": raw_path, "content": text, "truncated": truncated}, truncated=truncated)
        except UnicodeDecodeError as exc:
            return _failed(request, QUERY_FILE_NOT_UTF8, _error_text("query read", exc))
        except _ResolvedPathError as exc:
            return _failed(request, exc.reason_code, _error_text("query read", ValueError(exc.message)))
        except PermissionError as exc:
            return _failed(request, QUERY_PERMISSION_DENIED, _error_text("query read", exc))
        except (OSError, ValueError) as exc:
            return _failed(request, QUERY_IO_FAILED, _error_text("query read", exc))

    def find(self, request: WorkspaceQueryRequest, cancellation: QueryCancellation) -> WorkspaceQueryResult:
        from agent.application_services.query_find import execute_find

        return execute_find(self, request, cancellation)

    def git_status(self, request: WorkspaceQueryRequest, cancellation: QueryCancellation) -> WorkspaceQueryResult:
        from agent.application_services.query_git import execute_git_status

        return cast(WorkspaceQueryResult, execute_git_status(self, request, cancellation, run_git, MAX_GIT_OUTPUT_BYTES, QUERY_TIMEOUT_SECONDS))

    def diff(self, request: WorkspaceQueryRequest, cancellation: QueryCancellation) -> WorkspaceQueryResult:
        from agent.application_services.query_git import execute_diff

        return cast(WorkspaceQueryResult, execute_diff(self, request, cancellation, run_git, MAX_GIT_OUTPUT_BYTES, QUERY_TIMEOUT_SECONDS, MAX_DIFF_FILES))

    def execute(self, request: WorkspaceQueryRequest, cancellation: QueryCancellation) -> WorkspaceQueryResult:
        if not isinstance(request, WorkspaceQueryRequest):
            raise TypeError("request must be WorkspaceQueryRequest")
        return {
            WorkspaceQueryKind.LIST_FILES: self.list_files,
            WorkspaceQueryKind.READ: self.read_file,
            WorkspaceQueryKind.FIND: self.find,
            WorkspaceQueryKind.GIT_STATUS: self.git_status,
            WorkspaceQueryKind.DIFF: self.diff,
        }[request.kind](request, cancellation)


__all__ = ["MAX_DIFF_FILES", "MAX_FIND_FILE_BYTES", "MAX_FIND_FILES", "MAX_FIND_MATCHES", "MAX_FIND_PATTERN_CHARS", "MAX_FIND_SCAN_CHARS", "MAX_FIND_SCAN_ENTRIES", "MAX_GIT_OUTPUT_BYTES", "MAX_LIST_ITEMS", "MAX_LIST_SCAN_ENTRIES", "MAX_QUERY_OUTPUT_CHARS", "MAX_READ_BYTES", "QUERY_CANCELLED", "QUERY_FIND_PATTERN_EMPTY", "QUERY_FIND_PATTERN_INVALID", "QUERY_GIT_FAILED", "QUERY_GIT_UNAVAILABLE", "QUERY_INVALID_REQUEST", "QUERY_IO_FAILED", "QUERY_PATH_INVALID", "QUERY_PATH_NOT_FOUND", "QUERY_PATH_TYPE_INVALID", "QUERY_PERMISSION_DENIED", "QUERY_FILE_TYPE_UNSUPPORTED", "QUERY_FILE_NOT_UTF8", "QUERY_TIMEOUT", "QUERY_TIMEOUT_SECONDS", "QueryCancellation", "ReadOnlyWorkspaceQueryService", "WORKSPACE_QUERY_CONTRACT_VERSION", "WorkspaceQueryKind", "WorkspaceQueryRequest", "WorkspaceQueryResult", "WorkspaceQueryStatus", "_ResolvedPathError", "_cancel_requested", "_cancelled", "_error_text", "_failed", "_freeze", "_succeeded", "run_git"]

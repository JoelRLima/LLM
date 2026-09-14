"""Hardened Git subprocess adapter for the read-only query plane."""

from __future__ import annotations

import os
import time
from threading import Event, Thread
from typing import Any

from agent.interfaces.cli.query_plane import (
    MAX_DIFF_FILES,
    MAX_GIT_OUTPUT_BYTES,
    MAX_QUERY_OUTPUT_CHARS,
    QUERY_TIMEOUT_SECONDS,
    QueryRequest,
    QueryResult,
    _result,
    shutil,
    subprocess,
)
from agent.runtime.path_safety import WorkspacePathError
from agent.runtime.workspace_context import WorkspaceContext


class GitQueryMixin:
    workspace: WorkspaceContext

    @staticmethod
    def _git_environment() -> dict[str, str]:
        environment = dict(os.environ)
        environment.update(
            {
                "GIT_TERMINAL_PROMPT": "0",
                "GIT_OPTIONAL_LOCKS": "0",
                "GIT_PAGER": "cat",
                "PAGER": "cat",
                "GIT_EXTERNAL_DIFF": "",
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_CONFIG_SYSTEM": os.devnull,
            }
        )
        return environment

    @staticmethod
    def _drain(stream: Any, parts: list[bytes]) -> None:
        total = 0
        while True:
            chunk = stream.read(8192)
            if not chunk:
                return
            if total < MAX_GIT_OUTPUT_BYTES:
                parts.append(chunk[: MAX_GIT_OUTPUT_BYTES - total])
            total += len(chunk)

    def _start_git(self, command: list[str]) -> Any:
        return subprocess.Popen(
            command,
            cwd=str(self.workspace.root),
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=self._git_environment(),
        )

    def _collect_git(self, process: Any, cancel: Event) -> tuple[int, str, str]:
        assert process.stdout is not None and process.stderr is not None
        stdout_parts: list[bytes] = []
        stderr_parts: list[bytes] = []
        out_thread = Thread(target=self._drain, args=(process.stdout, stdout_parts), daemon=False)
        err_thread = Thread(target=self._drain, args=(process.stderr, stderr_parts), daemon=False)
        out_thread.start()
        err_thread.start()
        deadline = time.monotonic() + QUERY_TIMEOUT_SECONDS
        while process.poll() is None:
            if cancel.is_set():
                process.kill()
                break
            if time.monotonic() >= deadline:
                process.kill()
                out_thread.join(2)
                err_thread.join(2)
                if out_thread.is_alive() or err_thread.is_alive():
                    raise TimeoutError("pipe drain did not settle")
                raise TimeoutError(f"after {QUERY_TIMEOUT_SECONDS}s")
            cancel.wait(0.02)
        return_code = process.wait(timeout=2)
        out_thread.join(2)
        err_thread.join(2)
        if out_thread.is_alive() or err_thread.is_alive():
            raise TimeoutError("pipe drain did not settle")
        return (
            return_code,
            b"".join(stdout_parts).decode("utf-8", errors="replace"),
            b"".join(stderr_parts).decode("utf-8", errors="replace"),
        )

    def _git(self, request: QueryRequest, arguments: list[str], cancel: Event) -> QueryResult:
        executable = shutil.which("git")
        if executable is None:
            return _result(request, ok=False, error="query git: git not found")
        command = [executable, "-c", "core.fsmonitor=false", "--no-pager", *arguments]
        try:
            return_code, stdout, stderr = self._collect_git(self._start_git(command), cancel)
        except TimeoutError:
            return _result(request, ok=False, error=f"query git: timeout after {QUERY_TIMEOUT_SECONDS}s")
        except (OSError, subprocess.TimeoutExpired) as exc:
            return _result(request, ok=False, error=f"query git: {exc}")
        truncated = len(stdout.encode("utf-8")) > MAX_QUERY_OUTPUT_CHARS
        stdout = stdout[:MAX_QUERY_OUTPUT_CHARS]
        if cancel.is_set():
            return _result(request, ok=False, error="query git: cancelled")
        if return_code != 0:
            return _result(request, ok=False, error=f"query git: {(stderr or stdout).strip()[:2000]}")
        return _result(request, ok=True, data=stdout or "(no output)", truncated=truncated)

    def git_status(self, request: QueryRequest, cancel: Event) -> QueryResult:
        return self._git(request, ["status", "--short", "--branch", "--untracked-files=normal"], cancel)

    def diff(self, request: QueryRequest, cancel: Event) -> QueryResult:
        paths = request.arguments.get("paths", ())
        detail = bool(request.arguments.get("detail", False))
        arguments = ["diff"]
        if not detail:
            arguments.append("--numstat")
        arguments.extend(["--no-ext-diff", "--no-textconv", "--"])
        if isinstance(paths, (list, tuple)):
            for value in paths:
                try:
                    relative = self.workspace.relative(str(value)).as_posix()
                except (WorkspacePathError, ValueError):
                    return _result(request, ok=False, error=f"query diff: path outside workspace: {value}")
                if relative.startswith("-"):
                    return _result(request, ok=False, error="query diff: invalid pathspec")
                arguments.append(relative)
        result = self._git(request, arguments, cancel)
        if detail or not result.ok:
            return result
        raw = str(result.data or "")
        rows: list[dict[str, Any]] = []
        for line in raw.splitlines():
            parts = line.split("\t", 2)
            if len(parts) != 3:
                continue
            added = None if parts[0] == "-" else int(parts[0]) if parts[0].isdigit() else None
            deleted = None if parts[1] == "-" else int(parts[1]) if parts[1].isdigit() else None
            rows.append({"file": parts[2], "added": added, "deleted": deleted})
        truncated = result.truncated or len(rows) > MAX_DIFF_FILES
        return _result(
            request,
            ok=True,
            data={"file_count": len(rows), "files": rows[:MAX_DIFF_FILES], "truncated": truncated},
            truncated=truncated,
        )

"""Bounded, read-only repository-state observation for W13 P4."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Any, Mapping

from agent.runtime.workspace_context import WorkspaceContext

from .base import BaseSkill
from .process_environment import confined_process_environment
from .repository_state_filesystem_support import (
    _preflight_workspace as _preflight_workspace_impl,
)
from .repository_state_model_support import (
    entry_to_dict,
    snapshot_from_dict,
    snapshot_to_context_dict,
    validate_entry,
)
from .repository_state_parser_support import (
    _parse_porcelain_v2 as _parse_porcelain_v2_impl,
)
from .repository_state_parser_support import (
    _RepositoryStateError as _SupportRepositoryStateError,
)
from .shell_process import ShellProcessError, run_bounded_process

MAX_CONFIG_BYTES = 128 * 1024
MAX_METADATA_ENTRIES = 50_000
MAX_REPRESENTED_ENTRIES = 256
MAX_RENDER_CHARS = 4096
_OID_RE = re.compile(r"^(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})$")
_MODE_RE = re.compile(r"^[0-7]{6}$")
_STATUS_RE = re.compile(r"^[.MADRCUT]{2}$")
_SUBMODULE_RE = re.compile(r"^(?:N\.\.\.|S[.C][.M][.U])$")
_BRANCH_AB_RE = re.compile(r"^[+-][0-9]+ [+-][0-9]+$")
_SECTION_RE = re.compile(r"^([A-Za-z0-9_.-]+)(?:\s+\"([^\"]*)\")?$")
_KEY_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_UNAVAILABLE_REASONS = frozenset(
    {
        "NOT_GIT_REPOSITORY",
        "GIT_UNAVAILABLE",
        "WORKSPACE_ROOT_MISMATCH",
        "GITDIR_OUTSIDE_WORKSPACE",
        "UNSAFE_PATH",
        "TIMEOUT",
        "MALFORMED_PORCELAIN",
        "UNSAFE_REPOSITORY_CONFIG",
        "METADATA_LIMIT",
        "OUTPUT_LIMIT",
    }
)


class _RepositoryStateError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True)
class RepositoryStateEntry:
    path: str
    index_status: str
    worktree_status: str
    untracked: bool

    def __post_init__(self) -> None:
        validate_entry(self)

    def to_dict(self) -> dict[str, object]:
        return entry_to_dict(self)


@dataclass(frozen=True)
class RepositoryStateSnapshot:
    available: bool
    branch: str | None
    head: str | None
    entries: tuple[RepositoryStateEntry, ...]
    truncated: bool
    complete: bool
    total_entries_observed: int | None
    reason_code: str | None

    def __post_init__(self) -> None:
        if type(self.available) is not bool or type(self.truncated) is not bool:
            raise TypeError("repository snapshot flags must be boolean")
        if type(self.complete) is not bool:
            raise TypeError("repository snapshot complete must be boolean")
        if self.truncated and self.complete:
            object.__setattr__(self, "complete", False)
        if len(self.entries) > MAX_REPRESENTED_ENTRIES:
            raise ValueError("repository snapshot exceeds represented-entry cap")
        if self.total_entries_observed is not None and (
            type(self.total_entries_observed) is not int
            or self.total_entries_observed < 0
        ):
            raise ValueError("total_entries_observed is invalid")
        if self.reason_code is not None and self.reason_code not in _UNAVAILABLE_REASONS:
            raise ValueError("unknown repository snapshot reason")
        if self.available and self.reason_code is not None:
            raise ValueError("available snapshot cannot have an unavailable reason")
        if not self.available and self.reason_code is None:
            raise ValueError("unavailable snapshot requires a reason")

    @classmethod
    def unavailable(cls, reason_code: str) -> "RepositoryStateSnapshot":
        if reason_code not in _UNAVAILABLE_REASONS:
            raise ValueError("unknown repository snapshot reason")
        return cls(False, None, None, (), False, False, None, reason_code)

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "RepositoryStateSnapshot":
        return snapshot_from_dict(cls, value)

    def to_dict(self) -> dict[str, object]:
        return {
            "available": self.available,
            "branch": self.branch,
            "head": self.head,
            "entries": [entry_to_dict(entry) for entry in self.entries],
            "truncated": self.truncated,
            "complete": self.complete,
            "total_entries_observed": self.total_entries_observed,
            "reason_code": self.reason_code,
        }

    def to_context_dict(self, *, max_chars: int = MAX_RENDER_CHARS) -> dict[str, object]:
        return snapshot_to_context_dict(self, max_chars=max_chars)


def _fixed_status_argv() -> list[str]:
    return [
        "git",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.untrackedCache=false",
        "-c",
        "maintenance.auto=false",
        "-c",
        "gc.auto=0",
        "-c",
        "color.ui=false",
        "--no-pager",
        "status",
        "--porcelain=v2",
        "--branch",
        "-z",
        "--no-renames",
        "--no-ahead-behind",
        "--untracked-files=normal",
        "--ignore-submodules=all",
    ]


def _preflight_workspace(workspace: WorkspaceContext) -> Path:
    try:
        return _preflight_workspace_impl(
            workspace,
            max_metadata_entries=MAX_METADATA_ENTRIES,
        )
    except _SupportRepositoryStateError as exc:
        raise _RepositoryStateError(exc.reason_code) from exc


def _parse_porcelain_v2(root: Path, raw: bytes) -> RepositoryStateSnapshot:
    try:
        return _parse_porcelain_v2_impl(root, raw)
    except _SupportRepositoryStateError as exc:
        raise _RepositoryStateError(exc.reason_code) from exc


class RepositoryStateSkill(BaseSkill):
    name = "repository_state"
    description = "Consulta estruturada e somente leitura do estado Git local."

    def __init__(
        self,
        base_dir: str | Path = ".",
        *,
        workspace: WorkspaceContext | None = None,
        timeout: int = 20,
    ) -> None:
        self.workspace = workspace or WorkspaceContext.create(base_dir)
        self.base_dir = self.workspace.root
        self.timeout = timeout

    def get_schema(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        }

    def execute(self, args: dict[str, object]) -> dict[str, object]:
        del args
        return self._observe()

    def execute_with_context(
        self,
        args: dict[str, object],
        *,
        cancellation_token: Any | None = None,
        cancellation_event: Event | None = None,
    ) -> dict[str, object]:
        del args
        return self._observe(cancellation_token, cancellation_event)

    def _observe(
        self,
        cancellation_token: Any | None = None,
        cancellation_event: Event | None = None,
    ) -> dict[str, object]:
        try:
            git_dir = _preflight_workspace(self.workspace)
            environment = confined_process_environment(self.workspace)
            environment["GIT_DIR"] = str(git_dir)
            environment["GIT_WORK_TREE"] = str(self.workspace.root)
            result = run_bounded_process(
                _fixed_status_argv(),
                workspace=self.workspace.root,
                environment=environment,
                timeout=self.timeout,
                cancellation_token=cancellation_token,
                cancellation_event=cancellation_event,
                binary_output=True,
            )
            if result.returncode != 0:
                snapshot = RepositoryStateSnapshot.unavailable("GIT_UNAVAILABLE")
            else:
                stdout = result.stdout
                if not isinstance(stdout, bytes):
                    snapshot = RepositoryStateSnapshot.unavailable("MALFORMED_PORCELAIN")
                elif len(stdout) > 1_048_576:
                    snapshot = RepositoryStateSnapshot.unavailable("OUTPUT_LIMIT")
                else:
                    snapshot = _parse_porcelain_v2(self.workspace.root, stdout)
        except _RepositoryStateError as exc:
            snapshot = RepositoryStateSnapshot.unavailable(exc.reason_code)
        except FileNotFoundError:
            snapshot = RepositoryStateSnapshot.unavailable("GIT_UNAVAILABLE")
        except ShellProcessError as exc:
            reason = "TIMEOUT" if exc.status == "timed_out" else "GIT_UNAVAILABLE"
            snapshot = RepositoryStateSnapshot.unavailable(reason)
        except (OSError, RuntimeError, ValueError):
            snapshot = RepositoryStateSnapshot.unavailable("GIT_UNAVAILABLE")
        return {
            "ok": True,
            "done": True,
            "data": snapshot.to_dict(),
            "complete": snapshot.complete,
            "truncated": snapshot.truncated,
            "repository_state_reason": snapshot.reason_code,
        }


__all__ = [
    "MAX_CONFIG_BYTES",
    "MAX_METADATA_ENTRIES",
    "MAX_RENDER_CHARS",
    "MAX_REPRESENTED_ENTRIES",
    "RepositoryStateEntry",
    "RepositoryStateSkill",
    "RepositoryStateSnapshot",
]

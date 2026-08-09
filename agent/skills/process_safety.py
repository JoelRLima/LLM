"""Shared policy for the restricted, subprocess-backed command runner."""

from __future__ import annotations

import os
import re
import shlex
from collections.abc import Mapping, Sequence
from pathlib import Path

ALLOWED_SHELL_COMMANDS = {
    "ruff",
    "git log",
    "tree",
}

_SINGLE_TOKEN_ALLOWED = {
    command for command in ALLOWED_SHELL_COMMANDS if " " not in command
}
_TWO_TOKEN_ALLOWED = {
    tuple(command.split(" ", 1))
    for command in ALLOWED_SHELL_COMMANDS
    if " " in command
}


def split_command(command: str) -> list[str] | None:
    """Tokenize for ``shell=False`` without pretending to parse a shell."""

    try:
        tokens = shlex.split(command, posix=os.name != "nt")
    except ValueError:
        return None
    if os.name == "nt":
        normalized: list[str] = []
        for token in tokens:
            if len(token) >= 2 and token[0] == token[-1] and token[0] in {"'", '"'}:
                token = token[1:-1]
            normalized.append(token)
        tokens = normalized
    # The subprocess is never a shell. Reject shell-control syntax rather
    # than allowing a caller to rely on platform-specific interpretation.
    if any(marker in token for token in tokens for marker in (";", "|", "&", ">", "<")):
        return None
    return tokens


def is_shell_command_allowed(tokens: Sequence[str]) -> bool:
    if not tokens:
        return False
    first = tokens[0].casefold()
    if first in _SINGLE_TOKEN_ALLOWED:
        return True
    return len(tokens) >= 2 and (first, tokens[1].casefold()) in _TWO_TOKEN_ALLOWED


def _command_name(tokens: Sequence[str]) -> str:
    return Path(tokens[0]).name.casefold() if tokens else ""


_MAX_COUNT_RE = re.compile(r"^\d+$")
MAX_LOCAL_HISTORY_COUNT = 1000


def local_history_arguments(tokens: Sequence[str]) -> tuple[str, ...] | None:
    """Parse the small positive argument set supported by model-actionable Git."""

    if len(tokens) < 2 or tokens[0].casefold() != "git" or tokens[1].casefold() != "log":
        return None

    def bounded(value: str) -> bool:
        return (
            bool(_MAX_COUNT_RE.fullmatch(value))
            and len(value) <= len(str(MAX_LOCAL_HISTORY_COUNT))
            and int(value) <= MAX_LOCAL_HISTORY_COUNT
        )

    extras = list(tokens[2:])
    if not extras:
        return ()
    if len(extras) == 1:
        token = extras[0].casefold()
        if token.startswith("-") and bounded(token[1:]):
            return ("--max-count", token[1:])
        if token.startswith("-n") and bounded(token[2:]):
            return ("--max-count", token[2:])
        if token.startswith("--max-count=") and bounded(token.split("=", 1)[1]):
            return ("--max-count", token.split("=", 1)[1])
        return None
    if len(extras) == 2 and extras[0].casefold() in {"-n", "--max-count"}:
        return (
            ("--max-count", extras[1])
            if bounded(extras[1])
            else None
        )
    return None


def _path_traverses_workspace(candidate: Path, workspace_root: Path) -> bool:
    """Return whether any canonical component enters the controlled workspace."""

    absolute = Path(os.path.abspath(os.fspath(candidate)))
    current = Path(absolute.anchor)
    for component in absolute.parts[1:]:
        current /= component
        try:
            resolved = current.resolve()
        except (OSError, RuntimeError, ValueError):
            return True
        if resolved == workspace_root or resolved.is_relative_to(workspace_root):
            return True
    return False


def resolve_trusted_executable(
    command: str,
    environment: Mapping[str, str],
    workspace: str | os.PathLike[str],
) -> str | None:
    """Resolve an executable while rejecting workspace-controlled binaries."""

    command_path = Path(command)
    workspace_root = Path(workspace).resolve()
    if command_path.is_absolute():
        candidates = [command_path]
    else:
        path_entries = [
            Path(entry)
            for entry in environment.get("PATH", "").split(os.pathsep)
            if entry and Path(entry).is_absolute()
        ]
        if os.name == "nt" and not command_path.suffix:
            names = [
                command + extension
                for extension in environment.get("PATHEXT", "").split(os.pathsep)
                if extension
            ]
        else:
            names = [command]
        candidates = [directory / name for directory in path_entries for name in names]

    for candidate in candidates:
        try:
            # A workspace-controlled directory entry is not trusted merely
            # because it resolves to a file outside the workspace.  A
            # symlink/junction there can redirect an allowlisted name to an
            # unrelated executable (for example, ``ruff -> python``).
            # Reject the lexical candidate before following indirection, and
            # keep the existing final-target check for links from outside.
            lexical_candidate = Path(os.path.abspath(os.fspath(candidate)))
            if _path_traverses_workspace(lexical_candidate, workspace_root):
                continue
            resolved = candidate.resolve()
            if not resolved.is_file() or resolved.is_relative_to(workspace_root):
                continue
            if os.name == "nt":
                pathext = {
                    extension.casefold()
                    for extension in environment.get("PATHEXT", "").split(os.pathsep)
                    if extension
                }
                if resolved.suffix.casefold() not in pathext:
                    continue
            elif not os.access(resolved, os.X_OK):
                continue
        except (OSError, RuntimeError, ValueError):
            continue
        return str(resolved)
    return None


def git_read_only_error(tokens: Sequence[str]) -> str | None:
    """Keep GitSkill and ShellSkill on the same local-history-only surface."""

    if local_history_arguments(tokens) is None:
        return "Somente 'git log' local com max-count opcional esta disponivel na superficie model-actionable."
    return None


def unsafe_command_error(tokens: Sequence[str]) -> str | None:
    command = _command_name(tokens)
    lowered = {token.casefold().split("=", 1)[0] for token in tokens[1:]}
    if command == "git":
        return git_read_only_error(tokens)
    if command == "ruff":
        if len(tokens) < 2 or tokens[1].casefold() != "check":
            return "Only 'ruff check' is available in read-only mode."
        if lowered & {"--fix", "--fix-only", "--output-file", "--watch"}:
            return "Ruff mutation/watch modes are not allowed by ShellSkill."
        if "--config" in lowered:
            return "Explicit Ruff configuration is not allowed; validation is isolated."
    if command == "tree" and any(
        token.casefold() == "-o" or token.casefold().startswith("-o")
        for token in tokens[1:]
    ):
        return "tree output files are not allowed in read-only mode."
    return None


def shell_effect(tokens: Sequence[str]) -> str | None:
    if _command_name(tokens) == "ruff":
        return "execute_workspace_validation"
    return None


def hardened_command(tokens: Sequence[str]) -> tuple[str, ...]:
    """Disable avoidable hooks/caches without widening the command surface."""

    command = _command_name(tokens)
    if command == "git" and len(tokens) > 1:
        history_args = local_history_arguments(tokens)
        if tokens[1].casefold() == "log" and history_args is None:
            return tuple(tokens)
        hardened = [
            "git",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "core.untrackedCache=false",
            "-c",
            "log.showSignature=false",
            "-c",
            "log.diffMerges=off",
            "--no-lazy-fetch",
            "--no-pager",
            tokens[1],
            "--no-patch",
            "--pretty=medium",
            *(history_args or ()),
        ]
        return tuple(hardened)
    if command == "ruff" and len(tokens) > 1 and tokens[1].casefold() == "check":
        hardened = list(tokens)
        insertion = hardened.index("--") if "--" in hardened else 2
        options = [
            option
            for option in ("--isolated", "--no-cache", "--no-fix")
            if option not in hardened
        ]
        hardened[insertion:insertion] = options
        return tuple(hardened)
    return tuple(tokens)

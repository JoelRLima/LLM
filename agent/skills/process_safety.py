"""Shared policy for the restricted, subprocess-backed command runner."""

from __future__ import annotations

import os
import shlex
from collections.abc import Sequence
from pathlib import Path

ALLOWED_SHELL_COMMANDS = {
    "ruff",
    "git status",
    "git log",
    "git diff",
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


def git_read_only_error(tokens: Sequence[str]) -> str | None:
    """Keep GitSkill and ShellSkill on the same read-only Git surface."""

    forbidden = {
        "--ext-diff": "external diff",
        "--output": "output writes",
        "--textconv": "external text conversion",
        "-c": "inline configuration",
        "--config-env": "external configuration",
        "--exec-path": "executable path override",
        "--upload-pack": "external upload-pack",
    }
    for token in tokens[1:]:
        option = token.casefold().split("=", 1)[0]
        if option in forbidden:
            return f"Git option nao permitida em modo somente leitura: {forbidden[option]}."
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
        hardened = [
            "git",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "core.untrackedCache=false",
            "--no-pager",
            tokens[1],
            *tokens[2:],
        ]
        if tokens[1].casefold() in {"diff", "log"}:
            hardened[7:7] = ["--no-ext-diff", "--no-textconv"]
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

"""Shared confinement policy for subprocess-backed workspace skills."""

from __future__ import annotations

import shlex
from collections.abc import Sequence
from pathlib import Path

ALLOWED_SHELL_COMMANDS = {
    "pytest",
    "ruff",
    "mypy",
    "git status",
    "git log",
    "git diff",
    "echo",
    "type",
    "dir",
    "tree",
    "ls",
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
    """Tokenize without invoking a shell; malformed quoting fails closed."""

    try:
        return shlex.split(command)
    except ValueError:
        return None


def is_shell_command_allowed(tokens: Sequence[str]) -> bool:
    if not tokens:
        return False
    first = tokens[0].casefold()
    if first in _SINGLE_TOKEN_ALLOWED:
        return True
    return (
        len(tokens) >= 2
        and (first, tokens[1].casefold()) in _TWO_TOKEN_ALLOWED
    )


def _command_name(tokens: Sequence[str]) -> str:
    return Path(tokens[0]).name.casefold() if tokens else ""


def git_read_only_error(tokens: Sequence[str]) -> str | None:
    """Keep both GitSkill and ShellSkill on Git's read-only surface."""

    forbidden = {
        "--ext-diff": "diff externo",
        "--output": "escrita de output",
        "--textconv": "filtro externo",
    }
    for token in tokens[2:]:
        option = token.casefold().split("=", 1)[0]
        if option in forbidden:
            return f"Opção Git não permitida em modo somente leitura: {option}"
    return None


def unsafe_command_error(tokens: Sequence[str]) -> str | None:
    command = _command_name(tokens)
    lowered = {token.casefold().split("=", 1)[0] for token in tokens[1:]}
    if command == "git":
        return git_read_only_error(tokens)
    if command == "mypy" and "--install-types" in lowered:
        return "mypy --install-types não é permitido pela ShellSkill."
    if command == "pytest" and "--pastebin" in lowered:
        return "pytest --pastebin não é permitido pela ShellSkill."
    if command == "ruff" and len(tokens) > 1 and tokens[1].casefold() == "server":
        return "ruff server não é permitido pela ShellSkill."
    return None


def shell_effect(tokens: Sequence[str]) -> str | None:
    command = _command_name(tokens)
    lowered = {token.casefold().split("=", 1)[0] for token in tokens[1:]}
    if command == "pytest":
        return "execute_workspace_tests"
    if command == "ruff":
        if (
            len(tokens) > 1 and tokens[1].casefold() in {"clean", "format"}
            or bool({"--fix", "--fix-only", "--output-file"} & lowered)
        ):
            return "modify_workspace"
        return "execute_workspace_validation"
    if command == "mypy":
        if "--junit-xml" in lowered:
            return "write_validation_report"
        return "execute_workspace_validation"
    if command == "tree" and "-o" in lowered:
        return "write_command_output"
    return None


def hardened_command(tokens: Sequence[str]) -> tuple[str, ...]:
    """Disable avoidable caches and extension hooks without changing the cwd."""

    hardened = list(tokens)
    command = _command_name(tokens)
    if command == "git" and len(tokens) > 1:
        hardened = [
            "git",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "core.untrackedCache=false",
            tokens[1],
            *tokens[2:],
        ]
        if tokens[1].casefold() in {"diff", "log"}:
            hardened[6:6] = ["--no-ext-diff", "--no-textconv"]
    elif command == "pytest":
        insertion = hardened.index("--") if "--" in hardened else len(hardened)
        hardened[insertion:insertion] = ["-p", "no:cacheprovider"]
    elif command == "ruff" and len(tokens) > 1 and tokens[1].casefold() == "check":
        insertion = hardened.index("--") if "--" in hardened else len(hardened)
        hardened.insert(insertion, "--no-cache")
    elif command == "mypy":
        insertion = hardened.index("--") if "--" in hardened else len(hardened)
        hardened.insert(insertion, "--no-incremental")
    return tuple(hardened)

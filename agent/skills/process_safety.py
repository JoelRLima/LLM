"""Shared policy for the restricted, subprocess-backed command runner."""

from __future__ import annotations

import os
import shlex
from collections.abc import Mapping, Sequence
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
_SIGNATURE_FORMAT_MARKERS = ("%G?", "%GS", "%GK", "%GF", "%GP", "%GT", "%GG")


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


def _signature_format_error(value: str) -> str | None:
    if any(marker in value for marker in _SIGNATURE_FORMAT_MARKERS):
        return "Formatos de assinatura nao sao permitidos em modo somente leitura."
    return None


def _git_token_error(token: str) -> str | None:
    option = token.casefold().split("=", 1)[0]
    if option == "--show-signature":
        return "Verificacao de assinatura nao e permitida em modo somente leitura."
    if option in {"--format", "--pretty"} and "=" in token:
        return _signature_format_error(token.split("=", 1)[1])
    if token.casefold().startswith("-c"):
        return "Configuracao inline nao permitida em modo somente leitura."
    return None


def _separated_signature_error(tokens: Sequence[str]) -> str | None:
    for index, token in enumerate(tokens[1:-1], start=1):
        if token.casefold() in {"--format", "--pretty"}:
            error = _signature_format_error(tokens[index + 1])
            if error is not None:
                return error
    return None


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
        token_error = _git_token_error(token)
        if token_error is not None:
            return token_error
        option = token.casefold().split("=", 1)[0]
        if option in forbidden:
            return f"Git option nao permitida em modo somente leitura: {forbidden[option]}."
    return _separated_signature_error(tokens)


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
            "-c",
            "log.showSignature=false",
            "--no-pager",
            tokens[1],
            *tokens[2:],
        ]
        if tokens[1].casefold() in {"diff", "log"}:
            hardened[9:9] = ["--no-ext-diff", "--no-textconv"]
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

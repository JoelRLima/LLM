"""Fail-closed path parsing for subprocess command arguments."""

from __future__ import annotations

import os
import re
from collections.abc import Sequence
from pathlib import Path

from agent.runtime.workspace_context import WorkspaceContext

_PATH_OPTIONS: dict[str, frozenset[str]] = {
    "git": frozenset(
        {
            "--output",
            "--pathspec-from-file",
            "--rotate-to",
            "--skip-to",
        }
    ),
    "pytest": frozenset(
        {
            "-c",
            "--basetemp",
            "--confcutdir",
            "--deselect",
            "--ignore",
            "--ignore-glob",
            "--junitxml",
            "--rootdir",
        }
    ),
    "ruff": frozenset(
        {
            "--cache-dir",
            "--config",
            "--output-file",
            "--stdin-filename",
        }
    ),
    "mypy": frozenset(
        {
            "--cache-dir",
            "--config-file",
            "--custom-typeshed-dir",
            "--junit-xml",
            "--python-executable",
            "--shadow-file",
        }
    ),
    "tree": frozenset({"-o"}),
}

_CONCATENATED_PATH_OPTIONS: dict[str, tuple[str, ...]] = {
    "git": ("-O", "-L"),
    "pytest": ("-c",),
    "tree": ("-o",),
}

_SHELL_CONTROL_TOKENS = frozenset({";", "|", "||", "&&", ">", ">>", "<"})
_WINDOWS_DEVICE_NAMES = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{number}" for number in range(1, 10)),
        *(f"LPT{number}" for number in range(1, 10)),
    }
)
_WINDOWS_ABSOLUTE = re.compile(r"[A-Za-z]:[\\/]")


def _command_name(tokens: Sequence[str]) -> str:
    return Path(tokens[0]).name.casefold() if tokens else ""


def _path_values(raw: str, *, include_raw: bool) -> tuple[str, ...]:
    values: list[str] = [raw] if include_raw else []
    pending = [raw]
    seen = {raw}
    while pending:
        value = pending.pop()
        for separator in ("=", ",", ":"):
            if separator not in value:
                continue
            for suffix in value.split(separator)[1:]:
                if suffix and suffix not in seen:
                    seen.add(suffix)
                    pending.append(suffix)
                    values.append(suffix)
    normalized = raw.replace("\\", "/")
    for marker in ("../", "./", "~/"):
        index = normalized.find(marker)
        if index >= 0:
            values.append(raw[index:])
    match = _WINDOWS_ABSOLUTE.search(raw)
    if match:
        values.append(raw[match.start():])
    return tuple(dict.fromkeys(values))


def _path_error(workspace: WorkspaceContext, raw: str) -> str | None:
    candidate = raw.strip()
    if not candidate or candidate == "-":
        return None
    if candidate.startswith("@"):
        candidate = candidate[1:]
    if candidate.casefold().startswith("file:"):
        return f"URI de arquivo não permitida em comando confinado: {raw}"
    if os.name == "nt":
        device = Path(candidate).name.split(".", 1)[0].upper()
        if device in _WINDOWS_DEVICE_NAMES:
            return f"Dispositivo externo não permitido em comando confinado: {raw}"
    try:
        workspace.resolve(candidate)
    except (OSError, PermissionError, RuntimeError, ValueError):
        return f"Caminho fora do workspace não permitido: {raw}"
    return None


def _values_error(
    workspace: WorkspaceContext,
    raw: str,
    *,
    include_raw: bool,
) -> str | None:
    for value in _path_values(raw, include_raw=include_raw):
        error = _path_error(workspace, value)
        if error:
            return error
    return None


def _argument_path_state(
    token: str,
    workspace: WorkspaceContext,
    *,
    path_options: frozenset[str],
    concatenated: tuple[str, ...],
    after_options: bool,
) -> tuple[bool, str | None]:
    if token in _SHELL_CONTROL_TOKENS:
        return False, f"Operador de shell não permitido com shell=False: {token}"
    option_name, separator, _ = token.casefold().partition("=")
    if option_name in path_options:
        if not separator:
            return True, None
        raw_value = token.split("=", 1)[1]
        return False, _values_error(workspace, raw_value, include_raw=True)
    matched_prefix = next(
        (
            prefix
            for prefix in concatenated
            if token.startswith(prefix) and len(token) > len(prefix)
        ),
        None,
    )
    if matched_prefix:
        raw_value = token[len(matched_prefix) :]
        return False, _values_error(workspace, raw_value, include_raw=True)
    include_raw = (
        after_options
        or not token.startswith("-")
        or token.startswith("@")
    )
    return False, _values_error(workspace, token, include_raw=include_raw)


def workspace_argument_error(
    tokens: Sequence[str],
    workspace: WorkspaceContext,
    *,
    operand_start: int,
) -> str | None:
    """Reject explicit, embedded, response-file and symlink path escapes."""

    command = _command_name(tokens)
    path_options = _PATH_OPTIONS.get(command, frozenset())
    concatenated = _CONCATENATED_PATH_OPTIONS.get(command, ())
    expects_path = False
    after_options = False
    for token in tokens[operand_start:]:
        if expects_path:
            error = _values_error(workspace, token, include_raw=True)
            if error:
                return error
            expects_path = False
            continue
        if token == "--":
            after_options = True
            continue
        expects_path, error = _argument_path_state(
            token,
            workspace,
            path_options=path_options,
            concatenated=concatenated,
            after_options=after_options,
        )
        if error:
            return error
    if expects_path:
        return "Opção de caminho sem valor; comando rejeitado por segurança."
    return None


__all__ = ["workspace_argument_error"]

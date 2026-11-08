"""Shared decoder for the existing read-only command syntax."""

import shlex
from typing import Any

_QUERY_COMMAND_ALIASES = {
    "query.list_files": "list_files",
    "query.read": "read",
    "query.find": "find",
    "query.git_status": "git_status",
    "query.git_diff": "diff",
}


def _canonical_command_id(command_id: str) -> str:
    return _QUERY_COMMAND_ALIASES.get(command_id, command_id)


def query_arguments(text: str, command_id: str) -> dict[str, Any] | None:
    command_id = _canonical_command_id(command_id)
    try:
        tokens = shlex.split(text.strip())
    except ValueError as exc:
        return {"_error": str(exc)}
    if command_id == "list_files":
        return {"path": tokens[1] if len(tokens) > 1 else "."}
    if command_id == "read":
        return {"file_path": tokens[1]} if len(tokens) > 1 else None
    if command_id == "find":
        return {"pattern": " ".join(tokens[1:]), "path": "."} if len(tokens) > 1 else None
    if command_id == "git_status":
        return {}
    if command_id == "diff":
        detail = bool(tokens[1:] and tokens[1].casefold() in {"--full", "--detail", "full", "detail"})
        return {"paths": tuple(tokens[2:] if detail else tokens[1:]), "detail": detail}
    return None

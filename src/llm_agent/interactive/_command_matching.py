"""Command token matching, completion and path validation without registry ownership."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from llm_agent.interactive.commands import CliActionBinding, CliActionRegistry

def completion_items(self: CliActionRegistry, text_before_cursor: str) -> tuple[str, ...]:
    if not isinstance(text_before_cursor, str) or not text_before_cursor.startswith("/"):
        return ()
    needle = text_before_cursor.casefold()
    preferred = self.preferred_commands()
    if needle.endswith(" "):
        family = needle.rstrip()
        return tuple(item for item in preferred if item.startswith(family + " "))
    if " " in needle:
        return tuple(item for item in preferred if item.startswith(needle))
    return tuple(
        item
        for item in preferred
        if item.casefold().startswith(needle) and (" " not in item or item.split(" ", 1)[0].casefold() == needle)
    )


def match_parts(self: CliActionRegistry, text: str) -> tuple[str, CliActionBinding, tuple[str, ...], bool, str] | None:
    if not isinstance(text, str):
        return None
    value = text.lstrip()
    if not value:
        return None
    tokens = self._tokens(value)
    if not tokens:
        return None
    token_values = tuple(match.group(0).casefold() for match in tokens)
    for path, action_id, is_alias in self._path_items:
        if len(token_values) < len(path) or token_values[: len(path)] != path:
            continue
        end = tokens[len(path) - 1].end()
        raw_payload = value[end:].lstrip()
        binding = self._by_action[action_id]
        return action_id, binding, path, is_alias, raw_payload
    return None


def _valid_path(path: tuple[str, ...], *, preferred: bool) -> bool:
    if not path or any(not isinstance(token, str) or not token for token in path):
        return False
    if preferred and not path[0].startswith("/"):
        return False
    if not preferred and path[0].startswith("/") is False and len(path) > 1:
        return False
    if any(token.startswith("/") for token in path[1:]):
        return False
    return all(token.casefold() == token for token in path)

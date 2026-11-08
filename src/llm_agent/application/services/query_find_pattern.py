"""Validation of the existing bounded literal query syntax."""

from __future__ import annotations

from llm_agent.application.services.queries import (
    MAX_FIND_PATTERN_CHARS,
    QUERY_FIND_PATTERN_EMPTY,
    QUERY_FIND_PATTERN_INVALID,
    WorkspaceQueryRequest,
    WorkspaceQueryResult,
    _failed,
)

_FIND_LITERAL_META = frozenset("\\.^$*+?{}[]()|")


def pattern_error(request: WorkspaceQueryRequest, pattern: str) -> WorkspaceQueryResult | None:
    if not pattern:
        return _failed(request, QUERY_FIND_PATTERN_EMPTY, "query find: pattern is empty")
    if len(pattern) > MAX_FIND_PATTERN_CHARS or any(token in _FIND_LITERAL_META for token in pattern):
        return _failed(request, QUERY_FIND_PATTERN_INVALID, "query find accepts literal text only")
    return None

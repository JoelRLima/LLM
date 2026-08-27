"""Requirement-specific matching for canonical task observations."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from agent.planning.failure_policy import FailureClass, classify_failure
from agent.planning.task_semantics_types import _normalize_text
from agent.tools.result_completeness import (
    canonical_completeness,
    canonical_result_successful,
    exact_source_covers_whole_result,
)

_READ_TOOLS = frozenset({"file_reader", "code_analyzer", "directory_lister"})
_SEARCH_TOOLS = frozenset({"grep", "search"})
_COMPARE_TOOLS = frozenset({"compare", "diff", "code_analyzer"})


def complete_observation(result: Mapping[str, Any]) -> bool:
    return result_is_successful(result) and "data" in result and canonical_completeness(result)[0]


def exact_source_observation(result: Mapping[str, Any]) -> bool:
    """Whether a result can prove a complete whole-source observation."""

    return (
        result_is_successful(result)
        and "data" in result
        and exact_source_covers_whole_result(result)
    )


def _search_observation_is_provable(owner: Any, item: Any, result: Mapping[str, Any]) -> bool:
    if complete_observation(result):
        return True
    if not result_is_successful(result) or "data" not in result:
        return False
    _complete, truncated = canonical_completeness(result)
    if not truncated:
        return False
    objective = _normalize_text(str(getattr(owner, "objective", "") or ""))
    # An explicitly requested truncation is itself the observation to disclose.
    return item.kind == "search" and (
        "trunc" in objective or ("limite" in objective and "observ" in objective)
    )


def arg_path(args: Mapping[str, Any] | None) -> str | None:
    if not isinstance(args, Mapping):
        return None
    for key in ("file_path", "target", "path"):
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def arg_query(args: Mapping[str, Any] | None) -> str | None:
    if not isinstance(args, Mapping):
        return None
    for key in ("pattern", "query"):
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def same_identity(left: str | None, right: str | None) -> bool:
    if left is None or right is None:
        return False
    return left.strip().casefold() == right.strip().casefold()


def _matches_read(item: Any, tool: str, args: Mapping[str, Any] | None) -> bool:
    return tool in _READ_TOOLS and same_identity(item.target, arg_path(args))


def _matches_search(
    owner: Any,
    item: Any,
    tool: str,
    args: Mapping[str, Any] | None,
    evidence_ref: int | str | None,
) -> bool:
    if tool not in _SEARCH_TOOLS:
        return False
    query = arg_query(args)
    if item.query is not None:
        return same_identity(item.query, query)
    if item.query_source != "previous_read" or query is None:
        return False
    if type(evidence_ref) is not int:
        return False
    return any(
        type(ref) is int
        and ref < evidence_ref
        and entry.get("tool") in _READ_TOOLS
        and isinstance(entry.get("result"), Mapping)
        and exact_source_observation(entry["result"])
        and same_identity(str(entry["result"].get("data")), query)
        for ref, entry in getattr(owner, "_evidence_catalog", {}).items()
    )


def _matches_compare(item: Any, tool: str, args: Mapping[str, Any] | None) -> bool:
    return tool in _COMPARE_TOOLS and compare_args_match(item.operands, args)


def _matches_analyze(item: Any, tool: str, args: Mapping[str, Any] | None) -> bool:
    return tool in {"code_analyzer", "analyze"} and (
        same_identity(item.target, arg_path(args))
        or same_identity(item.query, arg_query(args))
    )


def matches_requirement(
    owner: Any,
    item: Any,
    tool: str,
    result: Mapping[str, Any],
    args: Mapping[str, Any] | None,
    *,
    evidence_ref: int | str | None = None,
) -> bool:
    if item.kind == "read":
        if not exact_source_observation(result):
            return False
    elif item.kind == "search":
        if not _search_observation_is_provable(owner, item, result):
            return False
    elif not complete_observation(result):
        return False
    matchers = {
        "read": lambda: _matches_read(item, tool, args),
        "search": lambda: _matches_search(owner, item, tool, args, evidence_ref),
        "compare": lambda: _matches_compare(item, tool, args),
        "analyze": lambda: _matches_analyze(item, tool, args),
    }
    matcher = matchers.get(item.kind)
    return bool(matcher()) if matcher is not None else False


def matches_fallback(
    item: Any,
    tool: str,
    result: Mapping[str, Any],
    args: Mapping[str, Any] | None,
) -> bool:
    return (
        tool in _READ_TOOLS
        and classify_failure(result) is FailureClass.LOCAL
        and same_identity(item.fallback_target, arg_path(args))
    )


def compare_args_match(operands: Sequence[str], args: Mapping[str, Any] | None) -> bool:
    if not isinstance(args, Mapping):
        return False
    pairs = [
        (args.get("left"), args.get("right")),
        (args.get("a"), args.get("b")),
        (args.get("file_a"), args.get("file_b")),
    ]
    raw_files = args.get("files")
    if isinstance(raw_files, (list, tuple)) and len(raw_files) == 2:
        pairs.append((raw_files[0], raw_files[1]))
    expected = tuple(operands)
    for left, right in pairs:
        if not isinstance(left, str) or not isinstance(right, str):
            continue
        actual = (left, right)
        if all(same_identity(x, y) for x, y in zip(expected, actual, strict=True)):
            return True
        if all(same_identity(x, y) for x, y in zip(expected, actual[::-1], strict=True)):
            return True
    return False


def result_is_successful(result: Mapping[str, Any]) -> bool:
    return canonical_result_successful(result)


__all__ = (
    "_READ_TOOLS",
    "arg_path",
    "complete_observation",
    "exact_source_observation",
    "matches_fallback",
    "matches_requirement",
    "result_is_successful",
    "same_identity",
)

"""Small terminal-obligation matching predicates."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent.planning.task_semantics_evidence import (
    _READ_TOOLS,
    arg_path,
    matches_fallback,
    same_identity,
)


def failure_matches_obligation(
    obligation: Any,
    observation: Mapping[str, Any],
) -> bool:
    tool = str(observation.get("tool", ""))
    result = observation["result"]
    args = observation.get("args") if isinstance(observation.get("args"), Mapping) else None
    if obligation.kind == "read":
        return tool in _READ_TOOLS and same_identity(obligation.target, arg_path(args))
    if obligation.kind == "search":
        return tool in {"grep", "search"} and (
            same_identity(obligation.query, args.get("pattern") if args else None)
            or same_identity(obligation.query, args.get("query") if args else None)
        )
    if obligation.kind == "analyze":
        return tool in {"code_analyzer", "analyze"} and (
            same_identity(obligation.target, arg_path(args))
            or same_identity(obligation.query, args.get("query") if args else None)
        )
    return obligation.kind == "fallback" and matches_fallback(obligation, tool, result, args)


__all__ = ["failure_matches_obligation"]

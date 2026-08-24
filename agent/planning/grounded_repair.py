"""Private helper for the bounded, grounded grep validation repair."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
from typing import Any, Dict, List, Optional

from agent.planning.provenance_validation import grounded_user_literal_narrowing


def try_grounded_grep_repair(
    plan: List[Dict[str, Any]],
    objective: str,
    index: int,
    repairable_fields: frozenset[str],
    accepts_repair: Callable[[Mapping[str, Any], Mapping[str, Any], frozenset[str]], bool],
    validate_candidate: Callable[[List[Dict[str, Any]]], Optional[List[Dict[str, Any]]]],
) -> bool:
    """Apply a validator-approved narrowed plan atomically."""

    if repairable_fields != frozenset({"pattern"}) or not 0 <= index < len(plan):
        return False
    step = plan[index]
    if not isinstance(step, Mapping) or step.get("tool") != "grep":
        return False
    raw_args = step.get("args")
    if not isinstance(raw_args, Mapping) or "pattern" not in raw_args:
        return False
    literal = grounded_user_literal_narrowing(
        rejected_value=raw_args.get("pattern"), objective=objective
    )
    if literal is None:
        return False
    candidate_step = deepcopy(dict(step))
    candidate_args = deepcopy(dict(raw_args))
    candidate_args["pattern"] = literal
    candidate_step["args"] = candidate_args
    raw_bindings = candidate_step.get("bindings")
    if isinstance(raw_bindings, Mapping) and "pattern" in raw_bindings:
        bindings = deepcopy(dict(raw_bindings))
        bindings.pop("pattern", None)
        if bindings:
            candidate_step["bindings"] = bindings
        else:
            candidate_step.pop("bindings", None)
    if not accepts_repair(step, candidate_step, repairable_fields):
        return False
    candidate = [deepcopy(item) for item in plan]
    candidate[index] = candidate_step
    accepted = validate_candidate(candidate)
    if accepted is None:
        return False
    plan[:] = accepted
    return True


__all__ = ["try_grounded_grep_repair"]

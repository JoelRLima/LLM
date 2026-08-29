"""Field-scoped validation-repair compatibility contracts."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from agent.planning.plan_model import ToolPlanStep


def repairable_fields(step: Any, problem: str) -> frozenset[str]:
    if not isinstance(step, (Mapping, ToolPlanStep)):
        return frozenset()
    patterns = (
        r"Argumento ['\"]([^'\"]+)['\"] requer proveniencia",
        r"Campo ['\"]([^'\"]+)['\"] obrigat",
        r"Campo .*ausente:\s*['\"]([^'\"]+)['\"]",
        r"missing required argument:\s*([A-Za-z0-9_.-]+)",
        r"argument ['\"]([^'\"]+)['\"]",
        r"'([A-Za-z0-9_.-]+)':\s*(?:esperado|valor)",
    )
    text = str(problem or "")
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return frozenset({match.group(1)})
    return frozenset()


def accepts_constrained_repair(
    original: Mapping[str, Any], candidate: Mapping[str, Any], fields: frozenset[str]
) -> bool:
    if not fields or not isinstance(candidate, Mapping):
        return False
    if any(key not in {"tool", "args", "bindings", "_step_id"} for key in candidate):
        return False
    if candidate.get("tool") != original.get("tool"):
        return False
    if "_step_id" in original and candidate.get("_step_id") not in (None, original.get("_step_id")):
        return False
    original_args = original.get("args")
    candidate_args = candidate.get("args")
    if not isinstance(original_args, Mapping) or not isinstance(candidate_args, Mapping):
        return False
    frozen = set(str(key) for key in original_args) - set(fields)
    if any(key not in candidate_args or candidate_args[key] != original_args[key] for key in frozen):
        return False
    if any(str(key) not in set(original_args) | set(fields) for key in candidate_args):
        return False
    original_bindings = original.get("bindings")
    candidate_bindings = candidate.get("bindings")
    original_bindings = original_bindings if isinstance(original_bindings, Mapping) else {}
    candidate_bindings = candidate_bindings if isinstance(candidate_bindings, Mapping) else {}
    for key, value in candidate_bindings.items():
        if str(key) not in fields and (key not in original_bindings or original_bindings[key] != value):
            return False
    return all(
        key in candidate_bindings and candidate_bindings[key] == value
        for key, value in original_bindings.items()
        if str(key) not in fields
    )


__all__ = ["accepts_constrained_repair", "repairable_fields"]

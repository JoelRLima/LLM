"""Mechanical binding and plan-fidelity projections for Block 7 attribution."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any, cast

from agent.evaluation.block7_attribution_decisions import decision_plan, tool_entries
from agent.evaluation.block7_attribution_models import BindingFact, ToolEntry

_SAFE_BINDING_TARGET = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_MAX_BINDING_PATH = 32
_MAX_BINDING_PATH_KEY = 128

BindingIssue = tuple[int, str, str | None]
BindingProfile = tuple[tuple[BindingFact, ...], tuple[BindingIssue, ...]]


def path_tuple(value: Any) -> tuple[str | int, ...] | None:
    if not isinstance(value, list) or len(value) > _MAX_BINDING_PATH:
        return None
    normalized: list[str | int] = []
    for segment in value:
        if type(segment) is int and 0 <= segment <= 1_000_000:
            normalized.append(segment)
            continue
        if _valid_path_key(segment):
            normalized.append(segment)
            continue
        return None
    return tuple(normalized)


def _valid_path_key(segment: Any) -> bool:
    return bool(
        isinstance(segment, str)
        and 0 < len(segment) <= _MAX_BINDING_PATH_KEY
        and not segment.startswith("__")
        and all(ord(char) >= 32 for char in segment)
    )


def binding_profile(payload: Mapping[str, Any], *, canonical: bool) -> BindingProfile | None:
    """Extract binding facts and structural violations from one plan."""

    plan = decision_plan(payload)
    if plan is None:
        return None
    entries = tool_entries(plan)
    if entries is None:
        return None
    positions = {id(entry.step): position for position, entry in enumerate(entries)}
    facts: list[BindingFact] = []
    issues: list[BindingIssue] = []
    for plan_index, raw_step in enumerate(plan):
        step_facts, step_issues = _step_profile(
            plan_index,
            raw_step,
            plan,
            entries,
            positions,
            canonical=canonical,
        )
        facts.extend(step_facts)
        issues.extend(step_issues)
    return tuple(facts), tuple(issues)


def _step_profile(
    plan_index: int,
    raw_step: Mapping[str, Any],
    plan: Sequence[Mapping[str, Any]],
    entries: Sequence[ToolEntry],
    positions: Mapping[int, int],
    *,
    canonical: bool,
) -> tuple[list[BindingFact], list[BindingIssue]]:
    if raw_step.get("kind") == "deferred_condition" or "bindings" not in raw_step:
        return [], []
    bindings = raw_step.get("bindings")
    if not isinstance(bindings, Mapping):
        return [], [(plan_index, "bindings_not_object", None)]
    raw_args = raw_step.get("args")
    args = cast(Mapping[str, Any], raw_args) if isinstance(raw_args, Mapping) else {}
    facts: list[BindingFact] = []
    issues: list[BindingIssue] = []
    for raw_target, raw_spec in bindings.items():
        fact, issue = _binding_fact(
            plan_index,
            raw_step,
            raw_target,
            raw_spec,
            args,
            plan,
            entries,
            positions,
            canonical=canonical,
        )
        facts.append(fact)
        if issue is not None:
            issues.append((plan_index, issue, fact.target or None))
    return facts, issues


def _binding_fact(
    plan_index: int,
    raw_step: Mapping[str, Any],
    raw_target: Any,
    raw_spec: Any,
    args: Mapping[str, Any],
    plan: Sequence[Mapping[str, Any]],
    entries: Sequence[ToolEntry],
    positions: Mapping[int, int],
    *,
    canonical: bool,
) -> tuple[BindingFact, str | None]:
    target = raw_target if isinstance(raw_target, str) else None
    issue = _binding_shape_issue(target, raw_spec, args)
    source_tool: str | None = None
    source_position: int | None = None
    path: tuple[str | int, ...] | None = None
    if issue is None and isinstance(raw_spec, Mapping):
        source_tool, source_position, issue = _binding_source(
            raw_spec.get("from_step"),
            plan_index,
            plan,
            entries,
            positions,
            canonical=canonical,
        )
        path = path_tuple(raw_spec.get("path"))
        if path is None and issue is None:
            issue = "invalid_path"
    return (
        BindingFact(
            plan_index=plan_index,
            tool=str(raw_step.get("tool", "")),
            target=target or "",
            source_tool=source_tool,
            source_tool_position=source_position,
            path=path,
            target_in_args=target in args,
        ),
        issue,
    )


def _binding_shape_issue(
    target: str | None, raw_spec: Any, args: Mapping[str, Any]
) -> str | None:
    if target is None or not _SAFE_BINDING_TARGET.fullmatch(target):
        return "invalid_target"
    if target in args:
        return "target_in_args"
    if not isinstance(raw_spec, Mapping):
        return "spec_not_object"
    if set(raw_spec) != {"from_step", "path"}:
        return "unsupported_binding_fields"
    return None


def _binding_source(
    source: Any,
    plan_index: int,
    plan: Sequence[Mapping[str, Any]],
    entries: Sequence[ToolEntry],
    positions: Mapping[int, int],
    *,
    canonical: bool,
) -> tuple[str | None, int | None, str | None]:
    if canonical:
        return _canonical_source(source, plan_index, entries, positions)
    return _raw_source(source, plan_index, plan, entries, positions)


def _canonical_source(
    source: Any,
    plan_index: int,
    entries: Sequence[ToolEntry],
    positions: Mapping[int, int],
) -> tuple[str | None, int | None, str | None]:
    if not isinstance(source, str) or not source:
        return None, None, "canonical_source_not_id"
    source_entry = next(
        (
            entry
            for entry in entries
            if entry.plan_index < plan_index and entry.step.get("_step_id") == source
        ),
        None,
    )
    if source_entry is None:
        return None, None, "source_not_previous"
    return source_entry.tool, positions.get(id(source_entry.step)), None


def _raw_source(
    source: Any,
    plan_index: int,
    plan: Sequence[Mapping[str, Any]],
    entries: Sequence[ToolEntry],
    positions: Mapping[int, int],
) -> tuple[str | None, int | None, str | None]:
    if type(source) is not int or source <= 0 or source > plan_index:
        return None, None, "source_not_previous"
    source_step = plan[source - 1]
    if not isinstance(source_step.get("tool"), str) or source_step.get("kind") == "deferred_condition":
        return None, None, "source_not_previous"
    source_entry = next((entry for entry in entries if entry.plan_index == source - 1), None)
    position = positions.get(id(source_entry.step)) if source_entry is not None else None
    return str(source_step["tool"]), position, None


def binding_fact_key(fact: BindingFact, entries: Sequence[ToolEntry]) -> tuple[int, str]:
    position = next(
        (index for index, entry in enumerate(entries) if entry.plan_index == fact.plan_index),
        fact.plan_index,
    )
    return position, fact.target


def plan_is_faithful(
    raw_payload: Mapping[str, Any], canonical_plan: Any, canonical_entries: Sequence[ToolEntry]
) -> bool | None:
    """Compare the raw and canonical plan projections mechanically."""

    raw_plan = decision_plan(raw_payload)
    if raw_plan is None or not isinstance(canonical_plan, (list, tuple)):
        return None
    raw_entries = tool_entries(raw_plan)
    if raw_entries is None or len(raw_entries) != len(canonical_entries):
        return False
    raw_profile = binding_profile(raw_payload, canonical=False)
    canonical_profile = binding_profile(
        {"action": "use_tools", "plan": list(canonical_plan)}, canonical=True
    )
    if raw_profile is None or canonical_profile is None:
        return False
    raw_facts, raw_issues = raw_profile
    canonical_facts, canonical_issues = canonical_profile
    if raw_issues or canonical_issues:
        return False
    return _entries_faithful(raw_entries, canonical_entries) and _facts_faithful(
        raw_facts,
        raw_entries,
        canonical_facts,
        canonical_entries,
    )


def _entries_faithful(raw: Sequence[ToolEntry], canonical: Sequence[ToolEntry]) -> bool:
    for raw_entry, canonical_entry in zip(raw, canonical, strict=True):
        if raw_entry.tool != canonical_entry.tool or raw_entry.conditional != canonical_entry.conditional:
            return False
        raw_args = raw_entry.step.get("args")
        canonical_args = canonical_entry.step.get("args")
        if _mapping_or_empty(raw_args) != _mapping_or_empty(canonical_args):
            return False
    return True


def _mapping_or_empty(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _facts_faithful(
    raw_facts: Sequence[BindingFact],
    raw_entries: Sequence[ToolEntry],
    canonical_facts: Sequence[BindingFact],
    canonical_entries: Sequence[ToolEntry],
) -> bool:
    raw_by_key = {binding_fact_key(fact, raw_entries): fact for fact in raw_facts}
    canonical_by_key = {
        binding_fact_key(fact, canonical_entries): fact for fact in canonical_facts
    }
    if set(raw_by_key) != set(canonical_by_key):
        return False
    return all(_fact_values_equal(fact, canonical_by_key[key]) for key, fact in raw_by_key.items())


def _fact_values_equal(raw: BindingFact, canonical: BindingFact) -> bool:
    return bool(
        raw.source_tool == canonical.source_tool
        and raw.source_tool_position == canonical.source_tool_position
        and raw.path == canonical.path
        and raw.target_in_args == canonical.target_in_args
    )


__all__ = ["binding_fact_key", "binding_profile", "plan_is_faithful"]

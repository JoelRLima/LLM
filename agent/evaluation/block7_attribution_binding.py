"""Binding-specific causal attribution for Block 7."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from agent.evaluation.block7_attribution_binding_profile import (
    BindingIssue,
    binding_fact_key,
    binding_profile,
)
from agent.evaluation.block7_attribution_decisions import (
    binding_failure,
    decision_tool_entries,
    matching_raw_decision,
    only_raw_decision,
    plan_ref,
    required_binding_target,
    validation_refs,
)
from agent.evaluation.block7_attribution_models import BindingFact, DecisionRecord, ToolEntry


def binding_attribution(
    evidence: Mapping[str, Any],
    failures: Sequence[str],
    records: Sequence[DecisionRecord],
    canonical_entries: Sequence[ToolEntry] | None,
) -> dict[str, Any]:
    if not binding_failure(failures):
        return {}
    target = required_binding_target(evidence, failures)
    raw_record, raw_entries = _raw_decision(records, canonical_entries)
    if raw_record is None or raw_record.payload is None or raw_entries is None:
        return {}
    validation = validation_refs(
        evidence,
        "binding",
        "inval",
        "validation",
        decision_ref=raw_record.evidence_ref,
        require_explicit_correlation=len(records) > 1,
    )
    raw_profile = binding_profile(raw_record.payload, canonical=False)
    if raw_profile is None:
        return {}
    raw_facts, raw_issues = raw_profile
    raw_issues = _add_missing_required_issue(raw_facts, raw_issues, target)
    relevant_issues = tuple(
        issue for issue in raw_issues if target is None or issue[2] in (None, target)
    )
    refs = [raw_record.evidence_ref]
    contract_target = _contract_target(target, relevant_issues, raw_facts)
    if contract_target is not None:
        refs.append(f"binding_contract:{contract_target}")
    if relevant_issues:
        return _raw_violation_result(refs, validation, canonical_entries)
    return _canonical_mismatch_result(
        evidence,
        refs,
        validation,
        raw_facts,
        raw_entries,
        canonical_entries,
    )


def _raw_decision(
    records: Sequence[DecisionRecord], canonical_entries: Sequence[ToolEntry] | None
) -> tuple[DecisionRecord | None, tuple[ToolEntry, ...] | None]:
    record = matching_raw_decision(records, canonical_entries)
    if record is not None:
        entries = decision_tool_entries(record.payload) if record.payload is not None else None
        return record, entries
    selected = only_raw_decision(records)
    return selected if selected is not None else (None, None)


def _add_missing_required_issue(
    facts: Sequence[BindingFact],
    issues: Sequence[BindingIssue],
    target: str | None,
) -> tuple[BindingIssue, ...]:
    if target is None:
        return tuple(issues)
    has_fact = any(fact.target == target for fact in facts)
    has_issue = any(issue_target == target for _, _, issue_target in issues)
    if has_fact or has_issue:
        return tuple(issues)
    return (*issues, (0, "required_binding_missing", target))


def _contract_target(
    target: str | None,
    issues: Sequence[BindingIssue],
    facts: Sequence[BindingFact],
) -> str | None:
    if target is not None:
        return target
    for plan_index, _issue, issue_target in issues:
        if issue_target:
            return str(issue_target)
        fact_target = next(
            (fact.target for fact in facts if fact.plan_index == plan_index and fact.target),
            None,
        )
        if fact_target:
            return str(fact_target)
    return None


def _raw_violation_result(
    refs: list[str],
    validation: Sequence[str],
    canonical_entries: Sequence[ToolEntry] | None,
) -> dict[str, Any]:
    if not validation:
        return {}
    canonical_ref = plan_ref(canonical_entries[0]) if canonical_entries else "canonical_plan:empty"
    refs.extend((canonical_ref, *validation))
    return {
        "model_behavior": {
            "signature": "canonical_binding_contract",
            "category": "capability",
            "contract_violation": True,
            "decision_evidence": True,
            "canonical_runtime_evidence": True,
            "source": "raw_decision_analyzer",
            "evidence_refs": refs,
        }
    }


def _canonical_mismatch_result(
    evidence: Mapping[str, Any],
    refs: list[str],
    validation: Sequence[str],
    raw_facts: Sequence[BindingFact],
    raw_entries: Sequence[ToolEntry],
    canonical_entries: Sequence[ToolEntry] | None,
) -> dict[str, Any]:
    canonical_plan = evidence.get("canonical_plan")
    if canonical_entries is None or not isinstance(canonical_plan, (list, tuple)):
        return {}
    profile = binding_profile({"action": "use_tools", "plan": list(canonical_plan)}, canonical=True)
    if profile is None:
        return {}
    canonical_facts, canonical_issues = profile
    mismatch = _canonical_mismatch(
        raw_facts,
        raw_entries,
        canonical_facts,
        canonical_entries,
        canonical_issues,
    )
    if mismatch is None:
        return {}
    entry = next(
        (item for item in canonical_entries if item.plan_index == mismatch.plan_index),
        None,
    )
    refs.extend((plan_ref(entry), *validation))
    return {
        "runtime_defect": {
            "proven": True,
            "reason_codes": ["canonical_binding_transformation_mismatch"],
            "evidence_refs": refs,
        }
    }


def _canonical_mismatch(
    raw_facts: Sequence[BindingFact],
    raw_entries: Sequence[ToolEntry],
    canonical_facts: Sequence[BindingFact],
    canonical_entries: Sequence[ToolEntry],
    canonical_issues: Sequence[BindingIssue],
) -> BindingFact | None:
    raw_by_key = {binding_fact_key(fact, raw_entries): fact for fact in raw_facts}
    canonical_by_key = {
        binding_fact_key(fact, canonical_entries): fact for fact in canonical_facts
    }
    mismatch = _value_mismatch(raw_by_key, canonical_by_key)
    if mismatch is not None:
        return mismatch
    different_keys = set(canonical_by_key) ^ set(raw_by_key)
    if different_keys:
        key = next(iter(different_keys))
        return canonical_by_key.get(key) or raw_by_key.get(key)
    issue_target = next((item[2] for item in canonical_issues if item[2]), None)
    if not canonical_issues:
        return None
    return next(
        (fact for fact in canonical_facts if issue_target is None or fact.target == issue_target),
        None,
    )


def _value_mismatch(
    raw_by_key: Mapping[tuple[int, str], BindingFact],
    canonical_by_key: Mapping[tuple[int, str], BindingFact],
) -> BindingFact | None:
    for key, raw_fact in raw_by_key.items():
        canonical_fact = canonical_by_key.get(key)
        if canonical_fact is None or _facts_differ(raw_fact, canonical_fact):
            return canonical_fact or raw_fact
    return None


def _facts_differ(raw: BindingFact, canonical: BindingFact) -> bool:
    return bool(
        raw.source_tool != canonical.source_tool
        or raw.source_tool_position != canonical.source_tool_position
        or raw.path != canonical.path
        or canonical.target_in_args
    )


__all__ = ["binding_attribution"]

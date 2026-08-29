"""Required-tool causal attribution from raw and canonical evidence."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from agent.evaluation.attribution_binding_profile import plan_is_faithful
from agent.evaluation.attribution_decisions import (
    decision_tool_entries,
    matching_raw_decision,
    only_raw_decision,
    plan_ref,
    required_tool,
    tool_sequence,
)
from agent.evaluation.attribution_models import DecisionRecord, ToolEntry


def required_tool_attribution(
    failures: Sequence[str],
    records: Sequence[DecisionRecord],
    canonical_entries: Sequence[ToolEntry] | None,
    canonical_plan: Any,
    invocation_entries: Sequence[tuple[int, Mapping[str, Any]]] | None,
) -> dict[str, Any]:
    required = required_tool(failures)
    if required is None or canonical_entries is None or invocation_entries is None:
        return {}
    record, raw_entries = _raw_entries(records, canonical_entries)
    if record is None or raw_entries is None or record.payload is None:
        return {}
    raw_entry = next((entry for entry in raw_entries if entry.tool == required), None)
    canonical_entry = next((entry for entry in canonical_entries if entry.tool == required), None)
    invoked = {str(item.get("tool")) for _, item in invocation_entries}
    refs = [record.evidence_ref, f"required_tool:{required}"]
    if raw_entry is not None and canonical_entry is None:
        refs.extend((plan_ref(canonical_entry), "invocation:none"))
        return _runtime_result("raw_required_tool_dropped_by_canonical_plan", refs)
    faithful = plan_is_faithful(record.payload, canonical_plan, canonical_entries)
    if _raw_omission(
        raw_entry,
        canonical_entry,
        required in invoked,
        raw_entries,
        canonical_entries,
        faithful,
    ):
        canonical_ref = plan_ref(canonical_entries[0]) if canonical_entries else "canonical_plan:empty"
        refs.extend((canonical_ref, "invocation:none"))
        return _model_result(refs)
    return {}


def _raw_entries(
    records: Sequence[DecisionRecord], canonical_entries: Sequence[ToolEntry]
) -> tuple[DecisionRecord | None, tuple[ToolEntry, ...] | None]:
    record = matching_raw_decision(records, canonical_entries)
    if record is not None and record.payload is not None:
        return record, decision_tool_entries(record.payload)
    selected = only_raw_decision(records)
    return selected if selected is not None else (None, None)


def _raw_omission(
    raw_entry: ToolEntry | None,
    canonical_entry: ToolEntry | None,
    invoked: bool,
    raw_entries: Sequence[ToolEntry],
    canonical_entries: Sequence[ToolEntry],
    faithful: bool | None,
) -> bool:
    return bool(
        raw_entry is None
        and canonical_entry is None
        and not invoked
        and tool_sequence(raw_entries) == tool_sequence(canonical_entries)
        and faithful is True
    )


def _runtime_result(reason: str, refs: list[str]) -> dict[str, Any]:
    return {
        "runtime_defect": {
            "proven": True,
            "reason_codes": [reason],
            "evidence_refs": refs,
        }
    }


def _model_result(refs: list[str]) -> dict[str, Any]:
    return {
        "model_behavior": {
            "signature": "missing_required_tool",
            "category": "capability",
            "contract_violation": True,
            "decision_evidence": True,
            "canonical_runtime_evidence": True,
            "source": "raw_decision_analyzer",
            "evidence_refs": refs,
        }
    }


__all__ = ["required_tool_attribution"]

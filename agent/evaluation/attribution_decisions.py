"""Bounded raw-decision and canonical-plan projections for H-series."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from agent.evaluation.attribution_models import DecisionRecord, ToolEntry
from agent.llm.structured_output import StructuredOutputError, extract_json_value

_MAX_RAW_DECISION_CHARS = 16_000
_PLAN_ACTIONS = frozenset({"use_tools", "continue_after_plan", "execute"})
_NO_TOOL_ACTIONS = frozenset({"direct_response", "final", "complete", "blocked"})


def decision_records(evidence: Mapping[str, Any]) -> tuple[DecisionRecord, ...]:
    """Return all recorded model decisions with stable, bounded references."""

    records: list[DecisionRecord] = []
    duplicate_refs: set[str] = set()
    seen_refs: set[str] = set()
    for bucket in ("model_decisions", "repair_decisions", "route_decisions"):
        raw_records = evidence.get(bucket, ())
        if not isinstance(raw_records, Sequence) or isinstance(raw_records, (str, bytes)):
            continue
        for raw_record in raw_records:
            if not isinstance(raw_record, Mapping):
                continue
            call_index = raw_record.get("call_index")
            if type(call_index) is not int or call_index <= 0:
                continue
            evidence_ref = f"model_decision:{call_index}"
            if evidence_ref in seen_refs:
                duplicate_refs.add(evidence_ref)
            seen_refs.add(evidence_ref)
            payload, raw_present = parse_raw_decision(raw_record)
            records.append(
                DecisionRecord(
                    evidence_ref=evidence_ref,
                    record=raw_record,
                    payload=payload,
                    raw_present=raw_present,
                )
            )
    return tuple(record for record in records if record.evidence_ref not in duplicate_refs)


def parse_raw_decision(record: Mapping[str, Any]) -> tuple[Mapping[str, Any] | None, bool]:
    """Parse one recorder response without retaining its transcript."""

    candidate: Any = None
    raw_present = False
    for key in ("response", "raw_response", "content"):
        if key in record:
            candidate = record.get(key)
            raw_present = candidate not in (None, "")
            break
    if candidate is None and isinstance(record.get("decision"), Mapping):
        candidate = record["decision"]
        raw_present = True
    if candidate is None and "action" in record:
        candidate = record
        raw_present = True
    payload = _decision_payload(candidate, raw_present)
    if payload is not None and "action" not in payload and "tool" in payload:
        payload = {"action": "tool", **payload}
    return payload, raw_present


def _decision_payload(candidate: Any, raw_present: bool) -> Mapping[str, Any] | None:
    if isinstance(candidate, Mapping):
        return dict(candidate)
    if not isinstance(candidate, str) or not candidate.strip():
        return None
    if len(candidate) > _MAX_RAW_DECISION_CHARS or not raw_present:
        return None
    try:
        parsed = extract_json_value(candidate)
    except StructuredOutputError:
        return None
    return dict(parsed) if isinstance(parsed, Mapping) else None


def raw_response_is_bounded(record: Mapping[str, Any]) -> bool:
    for key in ("response", "raw_response", "content"):
        value = record.get(key)
        if isinstance(value, str):
            return len(value) <= _MAX_RAW_DECISION_CHARS
    return True


def decision_plan(payload: Mapping[str, Any]) -> list[Mapping[str, Any]] | None:
    action = str(payload.get("action", "")).casefold()
    if action in _PLAN_ACTIONS:
        raw_plan = payload.get("plan")
        if not isinstance(raw_plan, (list, tuple)):
            return None
        if not all(isinstance(step, Mapping) for step in raw_plan):
            return None
        return list(raw_plan)
    if action == "tool":
        return [payload]
    if action in _NO_TOOL_ACTIONS:
        return []
    return None


def tool_entries(plan: Sequence[Mapping[str, Any]]) -> tuple[ToolEntry, ...] | None:
    entries: list[ToolEntry] = []
    for plan_index, raw_step in enumerate(plan):
        if not isinstance(raw_step, Mapping):
            return None
        if raw_step.get("kind") == "deferred_condition":
            on_true = raw_step.get("on_true")
            if not isinstance(on_true, Mapping) or not isinstance(on_true.get("tool"), str):
                return None
            entries.append(ToolEntry(plan_index, on_true, conditional=True))
            continue
        if not isinstance(raw_step.get("tool"), str) or not str(raw_step.get("tool")):
            return None
        entries.append(ToolEntry(plan_index, raw_step))
    return tuple(entries)


def decision_tool_entries(payload: Mapping[str, Any]) -> tuple[ToolEntry, ...] | None:
    plan = decision_plan(payload)
    return None if plan is None else tool_entries(plan)


def canonical_tool_entries(plan: Any) -> tuple[ToolEntry, ...] | None:
    if not isinstance(plan, (list, tuple)):
        return None
    if not all(isinstance(step, Mapping) for step in plan):
        return None
    return tool_entries(plan)


def tool_sequence(entries: Sequence[ToolEntry]) -> tuple[str, ...]:
    return tuple(entry.tool for entry in entries)


def plan_ref(entry: ToolEntry | None, *, missing: str = "missing") -> str:
    return f"canonical_plan:{entry.plan_index + 1}" if entry is not None else f"canonical_plan:{missing}"


def invocation_entries(
    evidence: Mapping[str, Any],
) -> tuple[tuple[int, Mapping[str, Any]], ...] | None:
    raw_history = evidence.get("invocation_evidence")
    if not isinstance(raw_history, (list, tuple)):
        return None
    result: list[tuple[int, Mapping[str, Any]]] = []
    for index, item in enumerate(raw_history, start=1):
        if not isinstance(item, Mapping) or not isinstance(item.get("tool"), str) or not item.get("tool"):
            return None
        result.append((index, item))
    return tuple(result)


def required_tool(failures: Sequence[str]) -> str | None:
    matches: list[str] = []
    for raw_code in failures:
        code = _failure_code(raw_code)
        match = re.fullmatch(r"required_tool_missing:([^:]+)", code)
        if match:
            matches.append(match.group(1))
    unique = tuple(dict.fromkeys(matches))
    return unique[0] if len(unique) == 1 and unique[0] else None


def _failure_code(raw_code: Any) -> str:
    code = str(raw_code)
    return code[len("evaluator:") :] if code.startswith("evaluator:") else code


def binding_failure(failures: Sequence[str]) -> bool:
    return any(_failure_code(code).startswith("canonical_binding") for code in failures)


def invalid_structured_failure(failures: Sequence[str]) -> bool:
    return any(_failure_code(code) == "invalid_structured_decision" for code in failures)


def required_binding_target(evidence: Mapping[str, Any], failures: Sequence[str]) -> str | None:
    for container in (
        evidence,
        evidence.get("binding_contract"),
        evidence.get("required_binding"),
        evidence.get("oracle_evidence"),
        evidence.get("oracle"),
    ):
        if not isinstance(container, Mapping):
            continue
        for key in ("binding_target", "required_binding_target", "target"):
            value = container.get(key)
            if isinstance(value, str) and value:
                return value
    for raw_code in failures:
        code = _failure_code(raw_code)
        if code.startswith("canonical_binding:") and code.split(":", 1)[1]:
            return code.split(":", 1)[1]
    return None


def validation_refs(
    evidence: Mapping[str, Any],
    *tokens: str,
    decision_ref: str,
    require_explicit_correlation: bool,
) -> tuple[str, ...]:
    raw_events = evidence.get("validation_evidence", ())
    if not isinstance(raw_events, (list, tuple)):
        return ()
    lowered_tokens = tuple(token.casefold() for token in tokens)
    correlated: list[str] = []
    uncorrelated: list[str] = []
    for index, event in enumerate(raw_events, start=1):
        if not isinstance(event, Mapping):
            continue
        text = " ".join(str(value) for value in event.values()).casefold()
        if not any(token in text for token in lowered_tokens):
            continue
        event_id = event.get("event_id", event.get("id", event.get("sequence", index)))
        event_ref = f"validation_event:{event_id}"
        correlation = _validation_decision_ref(event)
        if correlation is None:
            uncorrelated.append(event_ref)
        elif correlation == decision_ref:
            correlated.append(event_ref)
    if correlated:
        return tuple(dict.fromkeys(correlated))
    if require_explicit_correlation or len(uncorrelated) != 1:
        return ()
    return (uncorrelated[0],)


def _validation_decision_ref(event: Mapping[str, Any]) -> str | None:
    for key in ("model_decision_ref", "decision_ref"):
        value = event.get(key)
        if isinstance(value, str) and value.startswith("model_decision:"):
            return value
    for key in ("model_call_index", "decision_call_index", "call_index"):
        value = event.get(key)
        if type(value) is int and value > 0:
            return f"model_decision:{value}"
    return None


def matching_raw_decision(
    records: Sequence[DecisionRecord], canonical_entries: Sequence[ToolEntry] | None
) -> DecisionRecord | None:
    if canonical_entries is None:
        return None
    canonical_tools = tool_sequence(canonical_entries)
    parsed = _parsed_decisions(records)
    matches = [item for item in parsed if tool_sequence(item[1]) == canonical_tools]
    return matches[0][0] if len(matches) == 1 else None


def only_raw_decision(
    records: Sequence[DecisionRecord],
) -> tuple[DecisionRecord, tuple[ToolEntry, ...]] | None:
    parsed = _parsed_decisions(records)
    return parsed[0] if len(parsed) == 1 else None


def _parsed_decisions(
    records: Sequence[DecisionRecord],
) -> list[tuple[DecisionRecord, tuple[ToolEntry, ...]]]:
    parsed: list[tuple[DecisionRecord, tuple[ToolEntry, ...]]] = []
    for record in records:
        if record.payload is None:
            continue
        entries = decision_tool_entries(record.payload)
        if entries is not None:
            parsed.append((record, entries))
    return parsed


__all__ = [
    "binding_failure",
    "canonical_tool_entries",
    "decision_plan",
    "decision_records",
    "decision_tool_entries",
    "invalid_structured_failure",
    "invocation_entries",
    "matching_raw_decision",
    "only_raw_decision",
    "plan_ref",
    "raw_response_is_bounded",
    "required_binding_target",
    "required_tool",
    "tool_entries",
    "tool_sequence",
    "validation_refs",
]

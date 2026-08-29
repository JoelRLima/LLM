"""Mechanical oracle rules for H-series scenario observations."""

from __future__ import annotations

from typing import Any, Mapping

from agent.evaluation.oracle_observations import (
    answer,
    canonical_plan,
    evidence,
    observation,
    observation_flags,
    raw_duplicate_detected,
    result_status,
    validation_evidence,
)
from agent.evaluation.scenario_contracts import HSeriesArm


def basic_failures(
    oracle: Mapping[str, Any], history_items: list[Mapping[str, Any]], tools: list[str], report: Any
) -> list[str]:
    failures: list[str] = []
    for tool in oracle.get("required_tools", ()):
        if str(tool) not in tools:
            failures.append(f"required_tool_missing:{tool}")
    for tool in oracle.get("forbidden_tools", ()):
        if str(tool) in tools:
            failures.append(f"forbidden_tool_executed:{tool}")
    minimum = oracle.get("minimum_tool_calls")
    if isinstance(minimum, int) and len(history_items) < minimum:
        failures.append(f"tool_call_count_below_minimum:{minimum}")
    required_status = oracle.get("required_status")
    if required_status is not None and not any(
        isinstance(item.get("result"), Mapping)
        and result_status(item, item["result"]) == str(required_status)
        for item in history_items
    ):
        failures.append(f"required_status_missing:{required_status}")
    required_terminal = oracle.get("required_terminal_status")
    if required_terminal is not None and str(evidence(report).get("terminal_status") or "") != str(required_terminal):
        failures.append(f"required_terminal_status_missing:{required_terminal}")
    return failures


def _duplicate_rejection_observed(report: Any) -> bool:
    return any(
        event.get("type") in {"hard_block", "error"}
        and any(token in str(event).casefold() for token in ("colide", "duplicate", "binding", "inval"))
        for event in validation_evidence(report)
    )


def binding_failures(report: Any, oracle: Mapping[str, Any]) -> list[str]:
    target_raw = oracle.get("binding_target")
    target = str(target_raw) if target_raw is not None else None
    if target is None:
        return []
    saw_rejected_duplicate = False
    for step in canonical_plan(report):
        if step.get("tool") != "grep":
            continue
        bindings = step.get("bindings")
        spec = bindings.get(target) if isinstance(bindings, Mapping) else None
        if not isinstance(spec, Mapping):
            continue
        if "binding_path" in oracle and list(spec.get("path", ())) != list(oracle["binding_path"]):
            continue
        raw_args = step.get("args")
        args: Mapping[str, Any] = raw_args if isinstance(raw_args, Mapping) else {}
        if oracle.get("binding_target_absent_from_args") and target in args:
            if oracle.get("invalid_duplicate_must_not_execute"):
                saw_rejected_duplicate = True
            continue
        return []
    if (saw_rejected_duplicate or raw_duplicate_detected(report)) and _duplicate_rejection_observed(report):
        return []
    return ["canonical_binding_shape_missing"]


def observation_failures(
    report: Any, oracle: Mapping[str, Any], history_items: list[Mapping[str, Any]]
) -> list[str]:
    required = oracle.get("required_observation")
    failures: list[str] = []
    if not isinstance(required, Mapping):
        return failures
    matched = False
    for item in history_items:
        result = item.get("result")
        if not isinstance(result, Mapping):
            continue
        present, complete, truncated = observation_flags(result)
        checks = (("present", present), ("value", result.get("data")), ("complete", complete), ("truncated", truncated))
        if all(key not in required or value == required[key] for key, value in checks):
            matched = True
        if oracle.get("empty_is_not_failure") and result.get("data") == [] and result_status(item, result) in {"failed", "blocked", "unverified"}:
            failures.append("empty_observation_misclassified_as_failure")
    if not matched:
        failures.append("required_observation_shape_missing")
    return failures


def repair_failures(report: Any, oracle: Mapping[str, Any], history_items: list[Mapping[str, Any]]) -> list[str]:
    if not oracle.get("invalid_repair"):
        return []
    failures: list[str] = []
    if not evidence(report).get("repair_decisions", ()):
        failures.append("validation_repair_not_observed")
    if history_items:
        failures.append("invalid_repair_executed_a_tool")
    return failures


def _has_duplicate(step: Mapping[str, Any]) -> bool:
    args = step.get("args")
    bindings = step.get("bindings")
    return isinstance(args, Mapping) and isinstance(bindings, Mapping) and bool(set(args) & set(bindings))


def _duplicate_execution_failures(
    report: Any, invalid_steps: list[Mapping[str, Any]], history_items: list[Mapping[str, Any]], raw_duplicate: bool
) -> list[str]:
    failures: list[str] = []
    if not _duplicate_rejection_observed(report):
        failures.append("invalid_duplicate_rejection_evidence_missing")
    duplicate_tools = {str(step.get("tool")) for step in invalid_steps if step.get("tool") != "unknown"}
    if raw_duplicate and not duplicate_tools and history_items:
        failures.append("invalid_duplicate_executed")
    for item in history_items:
        if str(item.get("tool")) not in duplicate_tools:
            continue
        result = item.get("result")
        if not isinstance(result, Mapping) or result.get("executed") is not False:
            failures.append("invalid_duplicate_executed")
            break
    return failures


def duplicate_failures(
    report: Any, arm: HSeriesArm, oracle: Mapping[str, Any], history_items: list[Mapping[str, Any]]
) -> list[str]:
    if not oracle.get("invalid_duplicate_must_not_execute"):
        return []
    invalid_steps = [step for step in canonical_plan(report) if _has_duplicate(step)]
    raw_duplicate = raw_duplicate_detected(report)
    if raw_duplicate and not invalid_steps:
        invalid_steps = [{"tool": "unknown"}]
    failures = _duplicate_execution_failures(report, invalid_steps, history_items, raw_duplicate) if invalid_steps else []
    if arm.arm_id == "duplicate-rejected":
        if history_items:
            failures.append("invalid_duplicate_executed")
        current = observation(report)
        success = getattr(current, "success", None)
        if success is None and isinstance(current, Mapping):
            success = current.get("success")
        if success:
            failures.append("invalid_duplicate_reported_success")
    return failures


def effect_failures(report: Any, oracle: Mapping[str, Any], tools: list[str]) -> list[str]:
    if not oracle.get("forbidden_effects"):
        return []
    failures: list[str] = []
    receipt = evidence(report).get("receipt")
    outcome = receipt.get("operational_outcome") if isinstance(receipt, Mapping) else None
    if isinstance(outcome, Mapping) and outcome.get("mutation_occurred"):
        failures.append("forbidden_effect_observed")
    if "file_writer" in tools or "code_task" in tools:
        failures.append("forbidden_effect_tool_executed")
    return failures


def condition_failures(report: Any, oracle: Mapping[str, Any]) -> list[str]:
    expected = oracle.get("condition")
    if not isinstance(expected, bool):
        return []
    plan = canonical_plan(report)
    deferred = [step for step in plan if step.get("kind") == "deferred_condition"]
    if not deferred:
        return ["conditional_step_missing"]
    if expected is not False:
        return []
    failures: list[str] = []
    receipt = evidence(report).get("receipt")
    outcome = receipt.get("operational_outcome") if isinstance(receipt, Mapping) else None
    if isinstance(outcome, Mapping) and outcome.get("mutation_occurred"):
        failures.append("false_condition_mutated")
    waived = outcome.get("waived_effects", ()) if isinstance(outcome, Mapping) else ()
    if "write" not in waived:
        failures.append("false_condition_waiver_missing")
    on_false = deferred[0].get("on_false")
    if not isinstance(on_false, Mapping) or on_false.get("waive_effect") != "write":
        failures.append("false_condition_branch_missing")
    return failures


def route_failures(report: Any, oracle: Mapping[str, Any]) -> list[str]:
    if not oracle.get("required_route"):
        return []
    events = evidence(report).get("route_events", ())
    present = any(isinstance(event, Mapping) and event.get("type") == "hierarchical_started" for event in events)
    return [] if present else ["required_hierarchical_route_missing"]


def validation_failures(report: Any, oracle: Mapping[str, Any]) -> list[str]:
    required = oracle.get("required_validation")
    if not required:
        return []
    receipt = evidence(report).get("receipt")
    validation = receipt.get("validation") if isinstance(receipt, Mapping) else None
    valid = isinstance(validation, Mapping) and validation.get("outcome") == required
    return [] if valid else ["required_validation_outcome_missing"]


def rollback_failures(report: Any, oracle: Mapping[str, Any]) -> list[str]:
    expected = oracle.get("rollback_must_be_false")
    if expected not in (True, False):
        return []
    receipt = evidence(report).get("receipt")
    rollback = receipt.get("rollback") if isinstance(receipt, Mapping) else None
    occurred = rollback.get("occurred") if isinstance(rollback, Mapping) else None
    if expected is False and occurred is not True:
        return ["rollback_fact_missing"]
    if expected is True and occurred is True:
        return ["unexpected_rollback"]
    return []


def answer_failures(report: Any, oracle: Mapping[str, Any]) -> list[str]:
    current = answer(report).casefold()
    return [
        f"forbidden_answer:{text}"
        for text in oracle.get("forbidden_answer", ())
        if str(text).casefold() in current
    ]


def _grounding_kind_failures(
    kind: str, report: Any, oracle: Mapping[str, Any], results: list[Mapping[str, Any]], statuses: list[str], facts: dict[str, Any]
) -> list[str]:
    failures: list[str] = []
    if kind == "empty":
        facts["empty_observation"] = any(result.get("data") == [] for result in results)
        facts["tool_failure_observation"] = any(status in {"failed", "blocked"} for status in statuses)
        if not facts["empty_observation"]:
            failures.append("grounding_empty_observation_missing")
        if facts["tool_failure_observation"]:
            failures.append("grounding_empty_confused_with_tool_failure")
    elif kind == "tool_failure":
        facts["tool_failure_observation"] = any(status == "failed" for status in statuses)
        facts["empty_observation"] = any(result.get("data") == [] for result in results)
        if not facts["tool_failure_observation"]:
            failures.append("grounding_tool_failure_status_missing")
        if facts["empty_observation"]:
            failures.append("grounding_tool_failure_confused_with_empty")
    elif kind == "truncated":
        shapes = [observation_flags(result) for result in results]
        facts["truncated_observation"] = any(truncated and complete is False for _present, complete, truncated in shapes)
        if not facts["truncated_observation"]:
            failures.append("grounding_truncation_metadata_missing")
        if "trunc" not in answer(report).casefold():
            failures.append("grounding_truncation_disclosure_missing")
    else:
        failures.append(f"unknown_grounding_kind:{kind}")
    return failures


def grounding_failures(
    report: Any, oracle: Mapping[str, Any], history_items: list[Mapping[str, Any]]
) -> tuple[list[str], dict[str, Any]]:
    kind = oracle.get("grounding_kind")
    facts: dict[str, Any] = {"kind": kind, "fabricated_grounding": False}
    if not kind:
        return [], facts
    results = [item["result"] for item in history_items if isinstance(item.get("result"), Mapping)]
    statuses = [result_status(item, item["result"]) for item in history_items if isinstance(item.get("result"), Mapping)]
    failures = _grounding_kind_failures(str(kind), report, oracle, results, statuses, facts)
    current = answer(report).casefold()
    forbidden = tuple(str(item).casefold() for item in oracle.get("forbidden_answer", ()))
    if any(item in current for item in forbidden):
        facts["fabricated_grounding"] = True
        failures.append("fabricated_grounding_claim")
    return failures, facts


__all__ = [
    "answer_failures", "basic_failures", "binding_failures", "condition_failures",
    "duplicate_failures", "effect_failures", "grounding_failures", "observation_failures",
    "repair_failures", "rollback_failures", "route_failures", "validation_failures",
]

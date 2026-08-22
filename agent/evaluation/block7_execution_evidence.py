"""Identity, H2, and critical-incident projections for Block 7 evidence."""

from __future__ import annotations

from typing import Any, Mapping

from agent.evaluation.block7_execution_attribution import evidence_mapping


def identity_drift(expected: Mapping[str, Any], observed: Mapping[str, Any] | None) -> bool:
    if not isinstance(observed, Mapping):
        return True
    for key in ("provider", "model"):
        if str(observed.get(key, "")) != str(expected.get(key, expected.get("configured_model_id", ""))):
            return True
    expected_endpoint = expected.get("endpoint_identity")
    observed_endpoint = observed.get("endpoint_identity")
    if expected_endpoint and observed_endpoint and str(expected_endpoint) != str(observed_endpoint):
        return True
    expected_actual = expected.get("actual_provider_model_id")
    observed_actual = observed.get("actual_provider_model_id")
    if expected_actual is not None and str(expected_actual) != str(observed_actual):
        return True
    expected_caps = expected.get("capabilities")
    observed_caps = observed.get("capabilities")
    if isinstance(expected_caps, Mapping) and isinstance(observed_caps, Mapping):
        return any(
            key in expected_caps and bool(expected_caps[key]) != bool(observed_caps.get(key))
            for key in ("streaming", "reasoning", "token_counting", "tool_calls")
        )
    return False


def h2_reporting(report: Any, oracle_evidence: Mapping[str, Any]) -> dict[str, Any]:
    evidence = evidence_mapping(report)
    plan = evidence.get("canonical_plan")
    binding: Any = None
    pattern_in_args: bool | None = None
    if isinstance(plan, (list, tuple)):
        for step in plan:
            if not isinstance(step, Mapping) or step.get("tool") != "grep":
                continue
            bindings = step.get("bindings")
            if isinstance(bindings, Mapping) and isinstance(bindings.get("pattern"), Mapping):
                binding = dict(bindings["pattern"])
                args = step.get("args")
                pattern_in_args = isinstance(args, Mapping) and "pattern" in args
                break
    history = evidence.get("invocation_evidence", ())
    invocations = [dict(item) for item in history if isinstance(item, Mapping)]
    final_grep_args = None
    for item in invocations:
        if item.get("tool") == "grep" and isinstance(item.get("args"), Mapping):
            final_grep_args = dict(item["args"])
    return {
        "raw_initial_decision": list(evidence.get("model_decisions", ())),
        "raw_repair_decision": list(evidence.get("repair_decisions", ())),
        "accepted_canonical_plan": plan,
        "result_binding": binding,
        "pattern_in_args": pattern_in_args,
        "invocation_list": invocations,
        "final_grep_args": final_grep_args,
        "terminal_status_answer": {
            "status": evidence.get("terminal_status"),
            "answer": evidence.get("final_answer", ""),
        },
        "deterministic_grading": {
            "failures": list(oracle_evidence.get("failures", ())),
            "passed": not bool(oracle_evidence.get("failures")),
        },
    }


def critical_incidents(report: Any, oracle_evidence: Mapping[str, Any]) -> tuple[str, ...]:
    incidents: list[str] = []
    if oracle_evidence.get("forbidden_effect_incident"):
        incidents.append("forbidden_effect")
    grounding = oracle_evidence.get("grounding")
    if isinstance(grounding, Mapping) and grounding.get("fabricated_grounding"):
        incidents.append("fabricated_grounding")
    observation = getattr(report, "observation", None)
    evidence = getattr(observation, "evidence", {}) if observation is not None else {}
    status = evidence.get("terminal_status") if isinstance(evidence, Mapping) else None
    if status and status != "succeeded" and bool(getattr(observation, "success", False)):
        incidents.append("false_public_success")
    if status == "succeeded" and isinstance(evidence, Mapping):
        history = evidence.get("invocation_evidence", ())
        failed = any(
            isinstance(item, Mapping)
            and isinstance(item.get("result"), Mapping)
            and str(item["result"].get("status") or item.get("status") or "")
            in {"failed", "blocked", "unverified", "timed_out"}
            for item in history
        )
        if failed:
            incidents.append("false_public_success")
    return tuple(dict.fromkeys(incidents))


__all__ = ["critical_incidents", "h2_reporting", "identity_drift"]

"""Identity, H2, and critical-incident projections for H-series evidence."""

from __future__ import annotations

from typing import Any, Mapping

from agent.evaluation.execution_attribution import evidence_mapping
from agent.llm.identity import GENERIC_MODEL_ALIASES
from agent.runtime.outcome_taxonomy import NON_SUCCESS_STATUSES

_FAILURE_STATUSES = NON_SUCCESS_STATUSES


def _normalized_observed_identity(observed: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    if isinstance(observed, Mapping) and isinstance(observed.get("observed"), Mapping):
        observed = observed["observed"]
    return observed if isinstance(observed, Mapping) else None


def _scalar_identity_drift(expected: Mapping[str, Any], observed: Mapping[str, Any]) -> bool:
    for key in ("provider", "model"):
        observed_value = observed.get(key)
        if key == "model":
            observed_value = observed_value or observed.get("provider_model_id") or observed.get("actual_provider_model_id")
        if observed_value in (None, ""):
            continue
        if key == "model":
            expected_value = _specific_expected_model_id(expected)
            if expected_value is not None and str(observed_value) != expected_value:
                return True
            continue
        expected_value = expected.get(key)
        if expected_value in (None, ""):
            continue
        if str(observed_value) != str(expected_value):
            return True
    expected_endpoint = expected.get("endpoint_identity")
    observed_endpoint = observed.get("endpoint_identity")
    if expected_endpoint and observed_endpoint and str(expected_endpoint) != str(observed_endpoint):
        return True
    return False


def _specific_expected_model_id(expected: Mapping[str, Any]) -> str | None:
    for key in ("actual_provider_model_id", "model", "configured_model_id"):
        value = expected.get(key)
        if value in (None, ""):
            continue
        normalized = str(value)
        if normalized.casefold() not in GENERIC_MODEL_ALIASES:
            return normalized
    return None


def _capability_identity_drift(expected: Mapping[str, Any], observed: Mapping[str, Any]) -> bool:
    expected_caps = expected.get("capabilities")
    observed_caps = observed.get("capabilities")
    if not isinstance(expected_caps, Mapping) or not isinstance(observed_caps, Mapping):
        return False
    return any(
        key in expected_caps and bool(expected_caps[key]) != bool(observed_caps.get(key))
        for key in ("streaming", "reasoning", "token_counting", "tool_calls")
    )


def identity_drift(expected: Mapping[str, Any], observed: Mapping[str, Any] | None) -> bool:
    normalized = _normalized_observed_identity(observed)
    if normalized is None:
        return expected.get("actual_provider_model_id") is not None
    if normalized.get("available") is False:
        return expected.get("actual_provider_model_id") is not None
    if _scalar_identity_drift(expected, normalized):
        return True
    if _capability_identity_drift(expected, normalized):
        return True
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


def _operational_outcome(evidence: Mapping[str, Any]) -> Mapping[str, Any] | None:
    receipt = evidence.get("receipt")
    operational = receipt.get("operational_outcome") if isinstance(receipt, Mapping) else None
    if not isinstance(operational, Mapping):
        operational = evidence.get("operational_outcome")
    return operational if isinstance(operational, Mapping) else None


def _operational_unrecovered(operational: Mapping[str, Any]) -> bool:
    unrecovered = operational.get("unrecovered_failure")
    if type(unrecovered) is not bool:
        recovered = bool(
            operational.get("recovered_local_failure")
            or operational.get("fallback_resolved")
            or operational.get("failure_resolved")
        )
        unrecovered = bool(operational.get("pending_effects")) or (
            bool(operational.get("failed_invocation_ids")) and not recovered
        )
    return bool(
        unrecovered
        or operational.get("hard_failure") is True
        or operational.get("pending_effects")
    )


def _history_has_failure(evidence: Mapping[str, Any]) -> bool:
    history = evidence.get("invocation_evidence", ())
    return any(
        isinstance(item, Mapping)
        and isinstance(item.get("result"), Mapping)
        and str(item["result"].get("status") or item.get("status") or "") in _FAILURE_STATUSES
        for item in history
    )


def _public_success_incident(report: Any, evidence: Mapping[str, Any]) -> bool:
    observation = getattr(report, "observation", None)
    operational = _operational_outcome(evidence)
    status = (
        operational.get("terminal_status")
        if operational is not None
        else evidence.get("terminal_status")
    )
    if status and status != "succeeded" and bool(getattr(observation, "success", False)):
        return True
    if status != "succeeded":
        return False
    if operational is not None:
        return _operational_unrecovered(operational)
    return _history_has_failure(evidence)


def critical_incidents(report: Any, oracle_evidence: Mapping[str, Any]) -> tuple[str, ...]:
    incidents: list[str] = []
    if oracle_evidence.get("forbidden_effect_incident"):
        incidents.append("forbidden_effect")
    grounding = oracle_evidence.get("grounding")
    if isinstance(grounding, Mapping) and grounding.get("fabricated_grounding"):
        incidents.append("fabricated_grounding")
    evidence = evidence_mapping(report)
    if _public_success_incident(report, evidence):
        incidents.append("false_public_success")
    return tuple(dict.fromkeys(incidents))


__all__ = ["critical_incidents", "h2_reporting", "identity_drift"]

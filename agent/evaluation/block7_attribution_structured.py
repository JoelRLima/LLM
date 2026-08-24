"""Structured-output causal attribution for Block 7."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from agent.evaluation.block7_attribution_decisions import (
    decision_tool_entries,
    invalid_structured_failure,
    raw_response_is_bounded,
    validation_refs,
)
from agent.evaluation.block7_attribution_models import DecisionRecord


def structured_attribution(
    evidence: Mapping[str, Any],
    failures: Sequence[str],
    records: Sequence[DecisionRecord],
) -> dict[str, Any]:
    if not invalid_structured_failure(failures):
        return {}
    invalid = [
        record
        for record in records
        if record.raw_present
        and raw_response_is_bounded(record.record)
        and _structured_payload_invalid(record, evidence)
    ]
    if len(invalid) != 1:
        return {}
    record = invalid[0]
    validation = validation_refs(
        evidence,
        "structured",
        "json",
        "parse",
        decision_ref=record.evidence_ref,
        require_explicit_correlation=len(records) > 1,
    )
    if not _structured_contract_present(record.record) or not validation:
        return {}
    if not _repair_policy_compliant(record.record, evidence, record.evidence_ref):
        return {}
    return {
        "model_behavior": {
            "signature": "invalid_structured_decision",
            "category": "capability",
            "contract_violation": True,
            "decision_evidence": True,
            "canonical_runtime_evidence": True,
            "source": "raw_decision_analyzer",
            "evidence_refs": [
                record.evidence_ref,
                "structured_contract",
                *validation,
                "repair_policy",
            ],
        }
    }


def _structured_contract_present(record: Mapping[str, Any]) -> bool:
    request = record.get("request")
    for container in (record, request):
        if not isinstance(container, Mapping):
            continue
        if container.get("structured_contract_present") is True:
            return True
        contract = container.get("structured_contract") or container.get(
            "expected_structured_contract"
        )
        if isinstance(contract, Mapping) and contract:
            return True
    if not isinstance(request, Mapping):
        return False
    return str(request.get("structured_mode", "")).casefold() in {
        "json_schema",
        "gbnf",
        "json_prompt",
    }


def _repair_policy_compliant(
    record: Mapping[str, Any],
    evidence: Mapping[str, Any],
    decision_ref: str,
) -> bool:
    if record.get("repair_policy_compliant") is True:
        return True
    record_policy = record.get("repair_policy") or record.get("structured_repair_policy")
    if isinstance(record_policy, Mapping) and _policy_compliant(record_policy):
        return True
    policy = evidence.get("repair_policy") or evidence.get("structured_repair_policy")
    if (
        isinstance(policy, Mapping)
        and _policy_decision_ref(policy) == decision_ref
        and _policy_compliant(policy)
    ):
        return True
    return False


def _policy_decision_ref(policy: Mapping[str, Any]) -> str | None:
    value = policy.get("model_decision_ref", policy.get("decision_ref"))
    if isinstance(value, str) and value.startswith("model_decision:"):
        return value
    call_index = policy.get("model_call_index", policy.get("call_index"))
    if type(call_index) is int and call_index > 0:
        return f"model_decision:{call_index}"
    return None


def _policy_compliant(policy: Mapping[str, Any]) -> bool:
    if policy.get("compliant") is True:
        return True
    allowed = policy.get("allowed")
    applied = policy.get("applied")
    return bool(
        (allowed is False and applied in (False, None))
        or (allowed is True and applied is True)
    )


def _structured_payload_invalid(
    record: DecisionRecord, evidence: Mapping[str, Any]
) -> bool:
    if record.payload is None or decision_tool_entries(record.payload) is None:
        return True
    expected_action, required_keys = _structured_requirements(record.record)
    if expected_action is not None and str(record.payload.get("action", "")) != expected_action:
        return True
    return any(key not in record.payload for key in required_keys if isinstance(key, str))


def _structured_requirements(
    record: Mapping[str, Any],
) -> tuple[str | None, Sequence[Any]]:
    expected_action: str | None = None
    required_keys: Sequence[Any] = ()
    request = record.get("request")
    for container in (record, request):
        if not isinstance(container, Mapping):
            continue
        contract = container.get("structured_contract") or container.get(
            "expected_structured_contract"
        )
        sources = (container, contract) if isinstance(contract, Mapping) else (container,)
        for source in sources:
            expected_action, required_keys = _merge_requirements(
                source, expected_action, required_keys
            )
    return expected_action, required_keys


def _merge_requirements(
    source: Mapping[str, Any],
    expected_action: str | None,
    required_keys: Sequence[Any],
) -> tuple[str | None, Sequence[Any]]:
    candidate_action = source.get("expected_action", source.get("required_action"))
    if isinstance(candidate_action, str) and candidate_action:
        expected_action = candidate_action
    candidate_keys = source.get("required_keys")
    if isinstance(candidate_keys, (list, tuple)):
        required_keys = candidate_keys
    schema = source.get("schema")
    if isinstance(schema, Mapping) and isinstance(schema.get("required"), (list, tuple)):
        required_keys = schema["required"]
    return expected_action, required_keys


__all__ = ["structured_attribution"]

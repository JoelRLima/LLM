from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from agent.evaluation.execution_attribution import (
    classify_failure,
    derive_attribution_evidence,
)
from agent.evaluation.scenario_contracts import CausalFailureClass, EvidenceLevel


def _report(evidence: dict[str, Any], *, measurement: dict[str, Any] | None = None) -> Any:
    return SimpleNamespace(
        passed=False,
        observation=SimpleNamespace(
            measurement=measurement or {"status": "failed"},
            evidence=evidence,
        ),
    )


def _decision(payload: Any, *, call_index: int = 1, request: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "call_index": call_index,
        "stage": "decision",
        "request": request or {},
        "response": json.dumps(payload) if not isinstance(payload, str) else payload,
    }


def _classify(report: Any, failures: tuple[str, ...], attribution: dict[str, Any]) -> CausalFailureClass:
    return classify_failure(
        report,
        failures,
        EvidenceLevel.REAL_MODEL,
        attribution_evidence=attribution,
    ).classification


def test_pair_a1_raw_omission_is_model_capability() -> None:
    failures = ("required_tool_missing:grep",)
    report = _report(
        {
            "model_decisions": [_decision({"action": "use_tools", "plan": [{"tool": "file_reader", "args": {}}]})],
            "canonical_plan": [{"tool": "file_reader", "args": {}}],
            "invocation_evidence": [],
            "terminal_status": "failed",
        }
    )

    attribution = derive_attribution_evidence(report, failures, EvidenceLevel.REAL_MODEL)

    assert attribution["model_behavior"]["category"] == "capability"
    assert "model_decision:1" in attribution["model_behavior"]["evidence_refs"]
    assert "required_tool:grep" in attribution["model_behavior"]["evidence_refs"]
    assert _classify(report, failures, attribution) is CausalFailureClass.MODEL_CAPABILITY


def test_pair_a2_raw_tool_is_runtime_drop() -> None:
    failures = ("required_tool_missing:grep",)
    report = _report(
        {
            "model_decisions": [
                _decision(
                    {
                        "action": "use_tools",
                        "plan": [
                            {"tool": "file_reader", "args": {}},
                            {"tool": "grep", "args": {"pattern": "x"}},
                        ],
                    }
                )
            ],
            "canonical_plan": [{"tool": "file_reader", "args": {}}],
            "invocation_evidence": [{"tool": "file_reader", "invocation_id": "read-1"}],
            "terminal_status": "failed",
        }
    )

    attribution = derive_attribution_evidence(report, failures, EvidenceLevel.REAL_MODEL)

    assert attribution["runtime_defect"]["proven"] is True
    assert "raw_required_tool_dropped_by_canonical_plan" in attribution["runtime_defect"]["reason_codes"]
    assert _classify(report, failures, attribution) is CausalFailureClass.RUNTIME_DEFECT


def test_pair_b1_raw_binding_violation_is_model_capability() -> None:
    failures = ("canonical_binding_shape_missing",)
    report = _report(
        {
            "model_decisions": [
                _decision(
                    {
                        "action": "use_tools",
                        "plan": [
                            {"tool": "file_reader", "args": {}},
                            {
                                "tool": "grep",
                                "args": {},
                                "bindings": {"pattern": {"from_step": 0, "path": []}},
                            },
                        ],
                    }
                )
            ],
            "canonical_plan": [],
            "invocation_evidence": [],
            "validation_evidence": [{"id": "bind-1", "type": "hard_block", "reason": "binding inválido"}],
            "binding_contract": {"target": "pattern"},
            "terminal_status": "blocked",
        }
    )

    attribution = derive_attribution_evidence(report, failures, EvidenceLevel.REAL_MODEL)

    assert attribution["model_behavior"]["signature"] == "canonical_binding_contract"
    assert "validation_event:bind-1" in attribution["model_behavior"]["evidence_refs"]
    assert _classify(report, failures, attribution) is CausalFailureClass.MODEL_CAPABILITY


def test_pair_b2_valid_raw_binding_corrupted_by_canonical_runtime() -> None:
    failures = ("canonical_binding_shape_missing",)
    report = _report(
        {
            "model_decisions": [
                _decision(
                    {
                        "action": "use_tools",
                        "plan": [
                            {"tool": "file_reader", "args": {}},
                            {
                                "tool": "grep",
                                "args": {},
                                "bindings": {"pattern": {"from_step": 1, "path": []}},
                            },
                        ],
                    }
                )
            ],
            "canonical_plan": [
                {"_step_id": "step-1", "tool": "file_reader", "args": {}},
                {"_step_id": "step-2", "tool": "grep", "args": {"pattern": "literal"}},
            ],
            "invocation_evidence": [],
            "validation_evidence": [{"id": "bind-2", "type": "error", "reason": "canonical binding invalid"}],
            "binding_contract": {"target": "pattern"},
            "terminal_status": "failed",
        }
    )

    attribution = derive_attribution_evidence(report, failures, EvidenceLevel.REAL_MODEL)

    assert attribution["runtime_defect"]["proven"] is True
    assert "canonical_binding_transformation_mismatch" in attribution["runtime_defect"]["reason_codes"]
    assert _classify(report, failures, attribution) is CausalFailureClass.RUNTIME_DEFECT


def test_pair_c_missing_raw_decision_is_unknown() -> None:
    failures = ("required_tool_missing:grep",)
    report = _report(
        {
            "model_decisions": [{"call_index": 1, "request": {"structured_mode": "json_schema"}}],
            "canonical_plan": [{"tool": "file_reader", "args": {}}],
            "invocation_evidence": [],
            "terminal_status": "failed",
        }
    )

    attribution = derive_attribution_evidence(report, failures, EvidenceLevel.REAL_MODEL)

    assert "model_behavior" not in attribution
    assert "runtime_defect" not in attribution
    assert _classify(report, failures, attribution) is CausalFailureClass.UNKNOWN


def test_pair_d_proven_harness_failure_outranks_model_evidence() -> None:
    failures = ("required_tool_missing:grep",)
    report = _report(
        {
            "model_decisions": [_decision({"action": "use_tools", "plan": [{"tool": "file_reader", "args": {}}]})],
            "canonical_plan": [{"tool": "file_reader", "args": {}}],
            "invocation_evidence": [],
            "harness_defect": {"proven": True, "reason_codes": ["fixture_missing"]},
            "terminal_status": "failed",
        }
    )

    attribution = derive_attribution_evidence(report, failures, EvidenceLevel.REAL_MODEL)

    assert _classify(report, failures, attribution) is CausalFailureClass.HARNESS_DEFECT


def test_invalid_structured_decision_requires_contract_validation_and_policy() -> None:
    failures = ("evaluator:invalid_structured_decision",)
    report = _report(
        {
            "model_decisions": [
                _decision(
                    "not json",
                    request={
                        "structured_mode": "json_schema",
                        "structured_contract_present": True,
                    },
                )
            ],
            "canonical_plan": [],
            "invocation_evidence": [],
            "validation_evidence": [{
                "id": "structured-1",
                "type": "error",
                "reason": "invalid structured decision",
                "model_decision_ref": "model_decision:1",
            }],
            "repair_policy": {
                "compliant": True,
                "model_decision_ref": "model_decision:1",
            },
            "terminal_status": "failed",
        }
    )

    attribution = derive_attribution_evidence(report, failures, EvidenceLevel.REAL_MODEL)

    assert attribution["model_behavior"]["signature"] == "invalid_structured_decision"
    assert "validation_event:structured-1" in attribution["model_behavior"]["evidence_refs"]
    assert _classify(report, failures, attribution) is CausalFailureClass.MODEL_CAPABILITY


def test_unindexed_raw_decision_cannot_become_model_capability() -> None:
    failures = ("required_tool_missing:grep",)
    report = _report(
        {
            "model_decisions": [
                {
                    "stage": "decision",
                    "response": json.dumps(
                        {"action": "use_tools", "plan": [{"tool": "file_reader", "args": {}}]}
                    ),
                }
            ],
            "canonical_plan": [{"tool": "file_reader", "args": {}}],
            "invocation_evidence": [],
        }
    )

    attribution = derive_attribution_evidence(report, failures, EvidenceLevel.REAL_MODEL)

    assert "model_behavior" not in attribution
    assert _classify(report, failures, attribution) is CausalFailureClass.UNKNOWN


def test_duplicate_model_call_identity_is_causally_ambiguous() -> None:
    failures = ("required_tool_missing:grep",)
    report = _report(
        {
            "model_decisions": [
                _decision({"action": "use_tools", "plan": [{"tool": "file_reader", "args": {}}]}),
                _decision({"action": "use_tools", "plan": [{"tool": "grep", "args": {}}]}),
            ],
            "canonical_plan": [{"tool": "file_reader", "args": {}}],
            "invocation_evidence": [],
        }
    )

    attribution = derive_attribution_evidence(report, failures, EvidenceLevel.REAL_MODEL)

    assert "model_behavior" not in attribution
    assert _classify(report, failures, attribution) is CausalFailureClass.UNKNOWN


def test_unrelated_validation_event_cannot_prove_raw_binding_causation() -> None:
    failures = ("canonical_binding_shape_missing",)
    report = _report(
        {
            "model_decisions": [
                _decision(
                    {
                        "action": "use_tools",
                        "plan": [{
                            "tool": "grep",
                            "args": {},
                            "bindings": {"pattern": {"from_step": 0, "path": []}},
                        }],
                    }
                )
            ],
            "canonical_plan": [],
            "invocation_evidence": [],
            "validation_evidence": [
                {"id": "other-1", "type": "error", "reason": "provider timeout"}
            ],
            "binding_contract": {"target": "pattern"},
        }
    )

    attribution = derive_attribution_evidence(report, failures, EvidenceLevel.REAL_MODEL)

    assert "model_behavior" not in attribution
    assert _classify(report, failures, attribution) is CausalFailureClass.UNKNOWN


def test_structured_contract_and_policy_from_another_call_are_unknown() -> None:
    failures = ("evaluator:invalid_structured_decision",)
    report = _report(
        {
            "model_decisions": [_decision("not json")],
            "structured_contract": {"required_keys": ["action"]},
            "validation_evidence": [{
                "id": "structured-other",
                "type": "error",
                "reason": "invalid structured decision",
                "model_decision_ref": "model_decision:2",
            }],
            "repair_policy": {
                "compliant": True,
                "model_decision_ref": "model_decision:2",
            },
        }
    )

    attribution = derive_attribution_evidence(report, failures, EvidenceLevel.REAL_MODEL)

    assert "model_behavior" not in attribution
    assert _classify(report, failures, attribution) is CausalFailureClass.UNKNOWN


def test_canonical_tool_not_invoked_without_skip_proof_is_unknown() -> None:
    failures = ("required_tool_missing:grep",)
    raw = {"action": "use_tools", "plan": [{"tool": "grep", "args": {"pattern": "x"}}]}
    report = _report(
        {
            "model_decisions": [_decision(raw)],
            "canonical_plan": [{"tool": "grep", "args": {"pattern": "x"}}],
            "invocation_evidence": [],
            "terminal_status": "cancelled",
        }
    )

    attribution = derive_attribution_evidence(report, failures, EvidenceLevel.REAL_MODEL)

    assert "runtime_defect" not in attribution
    assert _classify(report, failures, attribution) is CausalFailureClass.UNKNOWN


def test_supplied_model_summary_cannot_bypass_raw_decision_analysis() -> None:
    failures = ("required_tool_missing:grep",)
    report = _report({})
    supplied = {
        "model_behavior": {
            "signature": "missing_required_tool",
            "category": "capability",
            "contract_violation": True,
            "decision_evidence": True,
            "canonical_runtime_evidence": True,
            "evidence_refs": [
                "model_decision:1",
                "required_tool:grep",
                "canonical_plan:1",
                "invocation:none",
            ],
        }
    }

    classification = classify_failure(
        report,
        failures,
        EvidenceLevel.REAL_MODEL,
        attribution_evidence=supplied,
    ).classification

    assert classification is CausalFailureClass.UNKNOWN

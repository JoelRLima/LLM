from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from typing import Any

import pytest

from agent.evaluation.block7 import H_SERIES, CausalFailureClass, EvidenceLevel
from agent.evaluation.block7_analysis import CampaignAnalysisError, analyze_campaign, validate_campaign_report
from agent.evaluation.block7_analysis_metrics import metric_summary
from agent.evaluation.block7_analysis_verdict import installed_acceptance_state
from agent.evaluation.block7_execution_attribution import classify_failure
from agent.evaluation.block7_execution_evidence import critical_incidents, identity_drift
from agent.evaluation.block7_identity import fake_model_identity, resume_compatible
from agent.evaluation.block7_oracle import deterministic_oracle_evidence
from agent.evaluation.trace import RecordingGateway
from agent.llm.contracts import ModelMessage, ModelRequest, ModelResponse, ProviderCapabilities
from tests.unit.evaluation.test_block7_corrective import _analysis_report


def _observation(*, status: str, success: bool, outcome: dict[str, Any] | None = None) -> Any:
    evidence: dict[str, Any] = {"terminal_status": status, "invocation_evidence": ()}
    if outcome is not None:
        evidence["receipt"] = {"operational_outcome": outcome}
    return SimpleNamespace(
        success=success,
        evidence=evidence,
        measurement={},
    )


def test_recovered_local_failure_is_not_false_public_success() -> None:
    recovered = _observation(
        status="succeeded",
        success=True,
        outcome={
            "terminal_status": "succeeded",
            "failed_invocation_ids": ["read-1"],
            "recovered_invocation_ids": ["read-1"],
            "recovered_local_failure": True,
            "unrecovered_failure": False,
            "fallback_resolved": True,
        },
    )
    assert "false_public_success" not in critical_incidents(SimpleNamespace(observation=recovered), {})

    hard_failure = _observation(status="failed", success=True)
    assert "false_public_success" in critical_incidents(SimpleNamespace(observation=hard_failure), {})


def test_attribution_requires_direct_model_contract_evidence() -> None:
    report = SimpleNamespace(
        passed=False,
        observation=SimpleNamespace(measurement={}, evidence={}),
    )
    model = classify_failure(
        report,
        ("required_tool_missing:grep",),
        EvidenceLevel.REAL_MODEL,
        attribution_evidence={
            "model_behavior": {
                "signature": "missing_required_tool",
                "category": "capability",
                "contract_violation": True,
                "decision_evidence": True,
                "canonical_runtime_evidence": True,
            }
        },
    )
    assert model.classification is CausalFailureClass.MODEL_CAPABILITY

    runtime = classify_failure(
        report,
        ("required_tool_missing:grep",),
        EvidenceLevel.REAL_MODEL,
        attribution_evidence={
            "runtime_defect": {"proven": True, "reason_codes": ["dropped_invocation"]},
        },
    )
    assert runtime.classification is CausalFailureClass.RUNTIME_DEFECT

    harness = classify_failure(
        report,
        ("fixture_missing",),
        EvidenceLevel.REAL_MODEL,
        attribution_evidence={"harness_defect": {"proven": True, "reason_codes": ["fixture_missing"]}},
    )
    assert harness.classification is CausalFailureClass.HARNESS_DEFECT

    ambiguous = classify_failure(
        report,
        ("required_tool_missing:grep",),
        EvidenceLevel.REAL_MODEL,
        attribution_evidence={
            "model_behavior": {
                "signature": "observed_model_failure",
                "category": "capability",
                "decision_evidence": True,
                "canonical_runtime_evidence": True,
            }
        },
    )
    assert ambiguous.classification is CausalFailureClass.UNKNOWN


def test_observed_identity_is_explicit_and_resume_bound() -> None:
    expected = fake_model_identity()
    observed = {
        "available": True,
        "provider_model_id": "block7-scripted",
        "actual_provider_model_id": "block7-scripted",
        "provider": "block7-scripted",
        "model": "block7-scripted",
        "endpoint_identity": "in-process://block7-scripted",
    }
    assert not identity_drift(expected, observed)
    changed = dict(observed, provider_model_id="block7-scripted-v2", actual_provider_model_id="block7-scripted-v2", model="block7-scripted-v2")
    assert identity_drift(expected, changed)
    assert identity_drift(expected, {"available": False})

    report = _analysis_report()
    current = dict(report)
    current["observed_model_identity"] = dict(report["observed_model_identity"])
    current["observed_model_identity"]["provider_model_id"] = "block7-scripted-v2"
    assert not resume_compatible(report, current)


def test_scripted_provider_response_captures_observed_model_id_without_probe() -> None:
    class Gateway:
        provider_name = "provider"
        model = "declared"
        endpoint_identity = "in-process://provider"
        provider_model_id = None
        profile = {"temperature": 0.0}
        capabilities = ProviderCapabilities(streaming=False)

        def complete(self, request):
            del request
            return ModelResponse(
                content="{}",
                provider_metadata={"observed_provider_model_id": "observed-v1"},
            )

        def count_tokens(self, text):
            return len(text)

    recorder = RecordingGateway(Gateway())
    recorder.complete(
        ModelRequest(
            messages=(ModelMessage("user", "test"),),
            model="declared",
            temperature=0.0,
            max_output_tokens=8,
        )
    )
    observed = recorder.export_evidence()["observed_provider_identity"]
    assert observed["available"] is True
    assert observed["provider_model_id"] == "observed-v1"
    assert observed["source"] == "response.provider_metadata"


@pytest.mark.parametrize(
    ("acceptance", "expected_state", "expected_verdict"),
    (
        (None, "missing", "INCONCLUSIVE"),
        ({"status": "failed", "acceptance": False, "mode": "clean-acceptance"}, "failed", "NOT_RELEASE_READY_ENVIRONMENT"),
        ({"status": "diagnostic", "offline": True}, "inconclusive", "INCONCLUSIVE"),
        ({"status": "passed", "acceptance": True, "mode": "clean-acceptance", "task_files_in_wheel": False}, "passed", "RELEASE_READY"),
    ),
)
def test_installed_acceptance_is_a_final_release_precondition(acceptance, expected_state, expected_verdict) -> None:
    assert installed_acceptance_state(acceptance) == expected_state
    report = _analysis_report()
    if acceptance is None:
        report.pop("installed_acceptance", None)
    else:
        report["installed_acceptance"] = acceptance
    analysis = analyze_campaign(report)
    assert analysis["release_verdict"] == expected_verdict


def test_metrics_use_canonical_invocation_owner_not_history_projection() -> None:
    runs = [
        {"evidence": {"measurement": {"tool_history_count": 99, "tool_calls": 0}}},
        {"evidence": {"measurement": {"tool_history_count": 99, "canonical_metrics": {"tool_calls": 1}}}},
    ]
    assert metric_summary(runs)["tool_calls"] == 1


def test_h12_grading_requires_only_expected_changed_file() -> None:
    arm = next(item for item in H_SERIES if item.h_id == "H12").arms[0]

    def report(changed_files: list[str]) -> Any:
        return SimpleNamespace(
            scenario_id="h12-modify-validate",
            changed_files=changed_files,
            passed=True,
            observation=SimpleNamespace(
                success=True,
                answer="valid",
                evidence={
                    "terminal_status": "succeeded",
                    "invocation_evidence": [{"tool": "code_task", "result": {"status": "succeeded"}}],
                    "receipt": {
                        "validation": {"outcome": "passed"},
                        "rollback": {"occurred": False},
                    },
                },
            ),
        )

    assert deterministic_oracle_evidence(report(["h12_module.py"]), arm)["failures"] == []
    assert "h12_collateral_mutation" in deterministic_oracle_evidence(
        report(["h12_module.py", "notes.txt"]), arm
    )["failures"]
    assert "h12_expected_mutation_missing" in deterministic_oracle_evidence(report([]), arm)["failures"]
    assert deterministic_oracle_evidence(
        report(["h12_module.py", "__pycache__/h12_module.pyc"]), arm
    )["failures"] == []


def test_campaign_envelope_rejects_unknown_missing_and_duplicate_semantics() -> None:
    report = _analysis_report()
    unknown = deepcopy(report)
    unknown["runs"][0]["h_id"] = "H99"
    assert not validate_campaign_report(unknown)["valid"]
    with pytest.raises(CampaignAnalysisError):
        analyze_campaign(unknown)

    duplicate = deepcopy(report)
    duplicate["runs"].append(deepcopy(duplicate["runs"][0]))
    assert any("duplicate_semantic_scenario_identity" in error for error in validate_campaign_report(duplicate)["errors"])

    missing = deepcopy(report)
    missing["runs"] = [run for run in missing["runs"] if run["h_id"] != "H12"]
    assert any("missing_h_id:H12" in error for error in validate_campaign_report(missing)["errors"])

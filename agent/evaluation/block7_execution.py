"""One isolated Block 7 execution and bounded evidence projection."""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, cast

from agent.approval import AutoApprove
from agent.evaluation.agent_executor import AgentApplicationScenarioExecutor, GatewayFactory
from agent.evaluation.block7 import (
    CausalFailureClass,
    EvidenceLevel,
    HRunEvidence,
    HSeriesArm,
    HSeriesScenario,
    digest_fixture,
    sanitize_evidence,
)
from agent.evaluation.block7_execution_attribution import (
    FailureAttribution,
    classify_failure,
    derive_attribution_evidence,
    evidence_mapping,
    exception_report,
    is_environmental_exception,
)
from agent.evaluation.block7_execution_evidence import (
    critical_incidents,
    h2_reporting,
    identity_drift,
)
from agent.evaluation.block7_identity import (
    candidate_identity_string,
    fake_model_identity,
    unavailable_observed_identity,
)
from agent.evaluation.block7_oracle import deterministic_oracle_evidence
from agent.evaluation.runner import CapabilityEvaluator


@dataclass(frozen=True)
class CampaignRun:
    h_id: str
    arm_id: str
    repetition: int
    passed: bool
    report: Mapping[str, Any]
    evidence: Mapping[str, Any]
    attempt: int = 1
    scenario_repetition: int | None = None
    valid_repetition: bool = True
    environmental: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "h_id": self.h_id,
            "arm_id": self.arm_id,
            "repetition": self.repetition,
            "attempt": self.attempt,
            "scenario_repetition": self.scenario_repetition,
            "valid_repetition": self.valid_repetition,
            "environmental": self.environmental,
            "passed": self.passed,
            "report": sanitize_evidence(dict(self.report)),
            "evidence": sanitize_evidence(dict(self.evidence)),
        }

    def mark_invalid_attempt(self, reason: str) -> "CampaignRun":
        evidence = dict(self.evidence)
        evidence.update({"valid_repetition": False, "scenario_repetition": None, "invalid_attempt_reason": reason})
        return CampaignRun(
            self.h_id,
            self.arm_id,
            self.repetition,
            self.passed,
            self.report,
            evidence,
            attempt=self.attempt,
            scenario_repetition=None,
            valid_repetition=False,
            environmental=self.environmental,
        )


def _report_projection(report: Any) -> dict[str, Any]:
    return {
        "scenario_id": report.scenario_id,
        "capability": report.capability,
        "passed_by_existing_evaluator": report.passed,
        "changed_files": list(report.changed_files),
        "failures": [{"code": item.code, "message": item.message} for item in report.failures],
        "observation": {
            "success": report.observation.success,
            "steps": report.observation.steps,
            "status": report.observation.measurement.get("status"),
            "answer": report.observation.answer,
            "error": report.observation.error,
        },
    }


def _classify_failure(report: Any, failures: tuple[str, ...], level: EvidenceLevel) -> CausalFailureClass:
    """Compatibility wrapper that never defaults an unexplained failure to model quality."""

    return classify_failure(report, failures, level).classification


def _run_one(
    scenario: HSeriesScenario,
    arm: HSeriesArm,
    repetition: int,
    *,
    gateway_factory: GatewayFactory,
    candidate: Mapping[str, str],
    epoch: str,
    evidence_level: EvidenceLevel,
    model_identity: Mapping[str, Any] | None = None,
    scenario_repetition: int | None = None,
    attempt: int | None = None,
) -> CampaignRun:
    capability_scenario = arm.to_capability_scenario(scenario.h_id)
    executor = AgentApplicationScenarioExecutor(gateway_factory, approval_policy=AutoApprove())
    expected_model_identity = dict(
        model_identity or (fake_model_identity() if evidence_level is EvidenceLevel.DETERMINISTIC else {})
    )
    attempt_number = attempt if attempt is not None else repetition
    report: Any = None
    try:
        with tempfile.TemporaryDirectory(prefix=f"block7-{scenario.h_id.lower()}-") as raw_root:
            report = CapabilityEvaluator(executor).evaluate(
                capability_scenario, Path(raw_root) / "workspace"
            )
            oracle_evidence = deterministic_oracle_evidence(report, arm)
            observation_evidence = evidence_mapping(report)
            raw_observed_model_identity = observation_evidence.get("observed_provider_identity")
            if isinstance(raw_observed_model_identity, Mapping):
                observed_model_identity: Mapping[str, Any] = cast(
                    Mapping[str, Any], raw_observed_model_identity
                )
            else:
                provider_identity = observation_evidence.get("provider_identity")
                observed_model_identity = (
                    cast(Mapping[str, Any], provider_identity.get("observed"))
                    if isinstance(provider_identity, Mapping)
                    and isinstance(provider_identity.get("observed"), Mapping)
                    else unavailable_observed_identity()
                )
            failures = tuple(
                [f"evaluator:{item.code}" for item in report.failures]
                + list(oracle_evidence["failures"])
            )
            attribution_evidence = derive_attribution_evidence(report, failures, evidence_level)
            if expected_model_identity and identity_drift(
                expected_model_identity, observation_evidence.get("provider_identity")
            ):
                failures += ("identity:model_config_drift",)
                attribution_evidence["runtime_defect"] = {
                    "proven": True,
                    "reason_codes": ["model_config_drift"],
                    "evidence_refs": ["model_identity", "provider_identity"],
                }
            attribution = classify_failure(
                report, failures, evidence_level, attribution_evidence=attribution_evidence
            )
            run_evidence = HRunEvidence(
                scenario_id=capability_scenario.scenario_id,
                repetition=attempt_number,
                epoch=epoch,
                candidate_identity=candidate_identity_string(candidate),
                model_fingerprint=expected_model_identity,
                evidence_level=evidence_level,
                objective=capability_scenario.objective,
                initial_fixture_digest=digest_fixture(capability_scenario.initial_files),
                model_decisions=tuple(observation_evidence.get("model_decisions", ())),
                repair_decisions=tuple(observation_evidence.get("repair_decisions", ())),
                route_decisions=tuple(observation_evidence.get("route_decisions", ())),
                canonical_plan=observation_evidence.get("canonical_plan"),
                invocation_evidence=tuple(observation_evidence.get("invocation_evidence", ())),
                terminal_status=observation_evidence.get("terminal_status"),
                final_answer=str(observation_evidence.get("final_answer", "")),
                measurement=dict(getattr(report.observation, "measurement", {})),
                deterministic_failures=failures,
                causal_classification=attribution.classification,
                causal_reason_codes=attribution.reason_codes,
                causal_evidence_refs=attribution.evidence_refs,
                attribution_evidence=attribution_evidence,
                observed_model_identity=dict(observed_model_identity),
                oracle_evidence=oracle_evidence,
                receipt=dict(observation_evidence.get("receipt", {})),
                validation_evidence=tuple(observation_evidence.get("validation_evidence", ())),
                h2_reporting=h2_reporting(report, oracle_evidence) if scenario.h_id == "H2" else {},
                critical_incidents=critical_incidents(report, oracle_evidence),
                scenario_repetition=scenario_repetition,
                attempt=attempt_number,
                valid_repetition=True,
            )
            return CampaignRun(
                h_id=scenario.h_id,
                arm_id=arm.arm_id,
                repetition=attempt_number,
                passed=not failures,
                report=_report_projection(report),
                evidence=run_evidence.to_dict(),
                attempt=attempt_number,
                scenario_repetition=scenario_repetition,
                valid_repetition=True,
                environmental=False,
            )
    except Exception as exc:
        environmental = is_environmental_exception(exc)
        attribution_evidence = {
            "environmental" if environmental else "harness_defect": {
                "concrete" if environmental else "proven": True,
                "reason": type(exc).__name__,
                "reason_codes": ["evaluator_execution_exception"],
                "evidence_refs": ["execution_exception", "provider_boundary"]
                if environmental else ["execution_exception"],
            }
        }
        report = exception_report(scenario, arm, exc)
        attribution = FailureAttribution(
            CausalFailureClass.ENVIRONMENTAL if environmental else CausalFailureClass.HARNESS_DEFECT,
            ("concrete_environment_failure",) if environmental else ("evaluator_execution_exception",),
            ("execution_exception",),
        )
        run_evidence = HRunEvidence(
            scenario_id=capability_scenario.scenario_id,
            repetition=attempt_number,
            epoch=epoch,
            candidate_identity=candidate_identity_string(candidate),
            model_fingerprint=expected_model_identity,
            evidence_level=evidence_level,
            objective=capability_scenario.objective,
            initial_fixture_digest=digest_fixture(capability_scenario.initial_files),
            measurement={
                "status": "unavailable" if environmental else "failed",
                "error_type": type(exc).__name__,
                "error": str(exc)[:500],
                "environmental_evidence": attribution_evidence.get("environmental"),
                "provider_identity": None,
                "token_usage_complete": False,
            },
            deterministic_failures=(f"execution_exception:{type(exc).__name__}",),
            causal_classification=attribution.classification,
            causal_reason_codes=attribution.reason_codes,
            causal_evidence_refs=attribution.evidence_refs,
            attribution_evidence=attribution_evidence,
            observed_model_identity={
                "available": False,
                "provider_model_id": None,
                "actual_provider_model_id": None,
                "model": None,
                "provider": None,
                "source": "unavailable",
            },
            scenario_repetition=scenario_repetition,
            attempt=attempt_number,
            valid_repetition=not environmental,
        )
        return CampaignRun(
            scenario.h_id,
            arm.arm_id,
            attempt_number,
            False,
            report,
            run_evidence.to_dict(),
            attempt=attempt_number,
            scenario_repetition=scenario_repetition,
            valid_repetition=not environmental,
            environmental=environmental,
        )


__all__ = [
    "CampaignRun", "FailureAttribution", "_classify_failure", "_run_one", "classify_failure",
]

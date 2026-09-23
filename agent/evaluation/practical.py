"""One-shot deterministic practical diagnostics on the canonical evaluator."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, Callable, cast

from agent.evaluation.comparison import (
    EVALUATION_COMPARISON_INCOMPATIBLE,
    EvaluationComparisonError,
    compare_practical_reports,
    compare_receipt_groups,
)
from agent.evaluation.contracts import (
    MAX_ENGINEERING_FAULT_JSON_BYTES,
    CapabilityScenario,
    FaultController,
    FaultEffect,
    FaultPlanV1,
    FaultPlanV1Error,
    FaultStepV1,
    parse_fault_json,
)
from agent.evaluation.evaluation_identity import candidate_identity as candidate_identity
from agent.evaluation.evaluation_identity import candidate_identity_string as candidate_identity_string
from agent.evaluation.evaluation_identity import fake_model_identity as fake_model_identity
from agent.evaluation.evaluation_identity import semantic_candidate_manifest as semantic_candidate_manifest
from agent.evaluation.evaluation_identity import semantic_manifest_hash as semantic_manifest_hash
from agent.evaluation.evidence import practical_scenario_record, sanitize_evidence, write_evidence_report
from agent.evaluation.experiment import ensure_receipt_measurement_identity, evaluation_context
from agent.evaluation.practical_projection_support import _prompt_projection
from agent.evaluation.practical_scenarios import (
    PRACTICAL_SET_VERSION,
    PRACTICAL_V1,
    _PracticalGatewayFactory,
    practical_fixture_identity,
    prepare_practical_application,
    prepare_practical_workspace,
)
from agent.evaluation.receipt import build_practical_receipt
from agent.evaluation.scenario_contracts import EvidenceLevel
from agent.evaluation.scripted_gateway import FaultingEvaluationGateway, ScriptedEvaluationGateway

GatewayDecorator = Callable[[Any], Any]

_FAULT_EXPERIMENT_ID = "w20-practical-v1-fault"
# This module owns the complete deterministic PRACTICAL receipt, comparison,
# and closed fault protocol so evaluation identity cannot drift into a helper.

_PRACTICAL_SCENARIO_IDS = tuple(f"PV1-{index:02d}" for index in range(1, 9))
_COMPARISON_EXPERIMENT_ID = "w20-persona-router-practical-v1"

def run_practical_scripted(
    repo_root: str | Path,
    *,
    output_path: str | Path | None = None,
    scenarios: tuple[CapabilityScenario, ...] = PRACTICAL_V1,
    profile_id: str = "current",
    fault_plan: FaultPlanV1 | None = None,
    gateway_decorator: GatewayDecorator | None = None,
    _experiment_id_override: str | None = None,
) -> dict[str, Any]:
    """Run exactly one deterministic execution for each practical scenario."""
    return _run_practical_scripted(
        repo_root,
        output_path=output_path,
        scenarios=scenarios,
        profile_id=profile_id,
        fault_plan=fault_plan,
        gateway_decorator=gateway_decorator,
        _experiment_id_override=_experiment_id_override,
    )


def compare_practical_profiles(repo_root: str | Path) -> dict[str, Any]:
    return _compare_practical_profiles(repo_root)


def _evaluation_context(profile_id: str, selected_fault_plan: FaultPlanV1, override: str | None) -> Any:
    return evaluation_context(
        profile_id,
        experiment_id=(_FAULT_EXPERIMENT_ID if selected_fault_plan.steps else override or "w20-practical-v1"),
        trial_id=f"fault-{selected_fault_plan.fingerprint[:16]}" if selected_fault_plan.steps else profile_id,
    )


def _effective_decorator(
    gateway_decorator: GatewayDecorator | None,
    fault_controller: FaultController,
) -> GatewayDecorator:
    def decorate(scripted: Any) -> Any:
        provider = gateway_decorator(scripted) if gateway_decorator is not None else scripted
        return FaultingEvaluationGateway(provider, fault_controller)

    return decorate


def _run_practical_scripted(
    repo_root: str | Path,
    *,
    output_path: str | Path | None = None,
    scenarios: tuple[CapabilityScenario, ...] = PRACTICAL_V1,
    profile_id: str = "current",
    fault_plan: FaultPlanV1 | None = None,
    gateway_decorator: GatewayDecorator | None = None,
    _experiment_id_override: str | None = None,
) -> dict[str, Any]:
    from agent.approval import AutoApprove
    from agent.evaluation.agent_executor import AgentApplicationScenarioExecutor
    from agent.evaluation.runner import CapabilityEvaluator

    root = Path(repo_root).resolve()
    ids = tuple(item.scenario_id for item in scenarios)
    if ids != tuple(f"PV1-{index:02d}" for index in range(1, 9)):
        raise ValueError("PRACTICAL-V1 exige exatamente PV1-01..PV1-08 em ordem")
    if profile_id not in {"current", "persona-reference-w18"}:
        raise ValueError("unknown PRACTICAL evaluation profile")
    selected_fault_plan = fault_plan or FaultPlanV1.empty()
    if not isinstance(selected_fault_plan, FaultPlanV1):
        raise TypeError("fault_plan must be FaultPlanV1")
    candidate_start = candidate_identity(root)
    candidate_start_value = candidate_identity_string(candidate_start)
    model_identity = fake_model_identity()
    from agent.evaluation.model_identity import planned_model_profile

    runtime_profile = planned_model_profile(root)
    experiment = _evaluation_context(profile_id, selected_fault_plan, _experiment_id_override)
    fault_controller = FaultController(selected_fault_plan)
    effective_decorator = _effective_decorator(gateway_decorator, fault_controller)

    records: list[dict[str, Any]] = []
    for scenario in scenarios:
        captured: dict[str, ScriptedEvaluationGateway] = {}
        executor = AgentApplicationScenarioExecutor(
            _PracticalGatewayFactory(scenario, captured, effective_decorator),
            approval_policy=AutoApprove(),
            variant_composition=experiment.profile.composition,
            prepare_workspace=lambda _objective, workspace, _scenario=scenario: prepare_practical_workspace(_scenario, workspace),
            prepare_application=lambda _objective, workspace, paths, _scenario=scenario: prepare_practical_application(_scenario, workspace, paths),
        )
        import tempfile

        with tempfile.TemporaryDirectory(prefix=f"evaluation-{scenario.scenario_id.lower()}-") as raw_root:
            scenario_report = CapabilityEvaluator(executor).evaluate(scenario, Path(raw_root) / "workspace")
        ensure_receipt_measurement_identity(scenario.scenario_id, scenario_report, experiment=experiment)
        scripted = captured.get("gateway")
        records.append(
            practical_scenario_record(
                scenario,
                scenario_report,
                _prompt_projection(scripted),
                experiment=experiment,
                candidate_identity_value=candidate_start_value,
                model_identity=model_identity,
                provider_call_count=len(scripted.calls) if scripted is not None else 0,
                fixture_identity=practical_fixture_identity(PRACTICAL_V1),
                evidence_level=EvidenceLevel.DETERMINISTIC.value,
            )
        )
    candidate_end = candidate_identity(root)
    candidate_end_value = candidate_identity_string(candidate_end)
    fault_summary = fault_controller.summary()
    report = {
        "schema_version": 1,
        "practical_set_version": PRACTICAL_SET_VERSION,
        "practical_fixture_identity": practical_fixture_identity(scenarios),
        "evidence_level": EvidenceLevel.DETERMINISTIC.value,
        "profile_id": profile_id,
        "experiment_id": experiment.experiment_id,
        "trial_id": experiment.trial_id,
        "variant_profile": experiment.profile.to_dict(),
        "candidate_start": candidate_start,
        "candidate_end": candidate_end,
        "candidate_start_identity": candidate_start_value,
        "candidate_end_identity": candidate_end_value,
        "candidate_unchanged": candidate_start == candidate_end,
        "semantic_manifest_hash": semantic_manifest_hash(semantic_candidate_manifest(root)),
        "model_identity": model_identity,
        "model_config_fingerprint": model_identity.get("model_config_fingerprint"),
        "runtime_profile_fingerprint": runtime_profile.get("runtime_profile_fingerprint"),
        "observed_model_identity": {"available": True, "provider_model_id": "scripted-evaluation", "source": "in-process scripted gateway"},
        "execution_policy": {
            "one_execution_per_scenario": True,
            "environmental_retry": False,
            "live_model_used": False,
            "provider_call_count": fault_controller.call_index if selected_fault_plan.steps else sum(int(item.get("provider_call_count", 0)) for item in records),
            "provider_call_budget": 16,
        },
        "execution_complete": len(records) == 8 and tuple(item["scenario_id"] for item in records) == tuple(f"PV1-{index:02d}" for index in range(1, 9)),
        "scenarios": records,
        "fault_plan_fingerprint": fault_summary["fault_plan_fingerprint"],
        "fault_steps": fault_summary["fault_steps"],
        "faults_requested": fault_summary["faults_requested"],
        "faults_triggered": fault_summary["faults_triggered"],
        "faults_untriggered": fault_summary["faults_untriggered"],
        "summary": {"total": len(records), "passed": sum(bool(item["passed"]) for item in records), "failed": sum(not bool(item["passed"]) for item in records), "unknown_failures": 0},
    }
    receipt_documents = [dict(cast(Mapping[str, object], item["receipt"])) for item in records]
    safe_report = cast(dict[str, Any], sanitize_evidence(report))
    safe_records = safe_report.get("scenarios")
    if isinstance(safe_records, list):
        for item, receipt in zip(safe_records, receipt_documents, strict=False):
            if isinstance(item, dict):
                item["receipt"] = receipt
    write_evidence_report(output_path, safe_report)
    return safe_report


_PRACTICAL_SCENARIO_IDS = tuple(f"PV1-{index:02d}" for index in range(1, 9))
_COMPARISON_EXPERIMENT_ID = "w20-persona-router-practical-v1"


def _compare_practical_profiles(repo_root: str | Path) -> dict[str, Any]:
    """Run and compare exactly the two frozen PRACTICAL profiles."""
    current = run_practical_scripted(repo_root, profile_id="current", _experiment_id_override=_COMPARISON_EXPERIMENT_ID)
    reference = run_practical_scripted(repo_root, profile_id="persona-reference-w18", _experiment_id_override=_COMPARISON_EXPERIMENT_ID)

    def envelopes(report: Mapping[str, Any]) -> tuple[Mapping[str, object], ...]:
        result: list[Mapping[str, object]] = []
        for item in report["scenarios"]:
            if not isinstance(item, Mapping) or not isinstance(item.get("receipt"), Mapping):
                raise EvaluationComparisonError(EVALUATION_COMPARISON_INCOMPATIBLE, "missing practical receipt")
            result.append({**dict(item["receipt"]), "candidate_identity": report["candidate_start_identity"], "model_identity": report["model_identity"]})
        return tuple(result)

    return compare_practical_reports(
        current,
        reference,
        scenario_ids=_PRACTICAL_SCENARIO_IDS,
        scenario_set_identity=practical_fixture_identity(PRACTICAL_V1),
        experiment_id=_COMPARISON_EXPERIMENT_ID,
        envelope_builder=envelopes,
        receipt_comparer=compare_receipt_groups,
    )


__all__ = [
    "FaultController",
    "FaultEffect",
    "FaultingEvaluationGateway",
    "FaultPlanV1",
    "FaultPlanV1Error",
    "FaultStepV1",
    "MAX_ENGINEERING_FAULT_JSON_BYTES",
    "build_practical_receipt",
    "compare_practical_profiles",
    "parse_fault_json",
    "run_practical_scripted",
]

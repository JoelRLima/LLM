"""One-shot deterministic practical diagnostics on the canonical evaluator."""

from __future__ import annotations

import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Callable, cast

from agent.approval import AutoApprove
from agent.evaluation.agent_executor import AgentApplicationScenarioExecutor
from agent.evaluation.contracts import CapabilityScenario
from agent.evaluation.evaluation_identity import (
    candidate_identity,
    candidate_identity_string,
    fake_model_identity,
    semantic_candidate_manifest,
    semantic_manifest_hash,
)
from agent.evaluation.evidence import digest_fixture, sanitize_evidence
from agent.evaluation.practical_gateway_logic import practical_final_answer
from agent.evaluation.practical_oracle_support import _oracle_failures
from agent.evaluation.practical_projection_support import _prompt_projection
from agent.evaluation.practical_scenarios import (
    PRACTICAL_SET_VERSION,
    PRACTICAL_V1,
    practical_fixture_identity,
    prepare_practical_application,
    prepare_practical_workspace,
)
from agent.evaluation.runner import CapabilityEvaluator
from agent.evaluation.scenario_contracts import EvidenceLevel
from agent.evaluation.scripted_gateway import RecordingGateway, ScriptedEvaluationGateway

GatewayFactory = Callable[[str, Path], Any]




class _PracticalGatewayFactory:
    def __init__(
        self,
        scenario: CapabilityScenario,
        captured: dict[str, ScriptedEvaluationGateway],
    ) -> None:
        self.scenario = scenario
        self.captured = captured

    def __call__(self, objective: str, _workspace: Path) -> Any:
        gateway = ScriptedEvaluationGateway(
            objective,
            fixture_marker=self.scenario.scenario_id,
        )
        self.captured["gateway"] = gateway
        return RecordingGateway(gateway)


def _scenario_record(
    scenario: CapabilityScenario,
    report: Any,
    prompt: Mapping[str, Any],
) -> dict[str, Any]:
    failures, observed = _oracle_failures(scenario, report, prompt=prompt)
    evaluator_failures = [f"evaluator:{item.code}" for item in report.failures]
    all_failures = list(dict.fromkeys(evaluator_failures + failures))
    return {
        "scenario_id": scenario.scenario_id,
        "fixture_digest": digest_fixture(scenario.initial_files),
        "passed": not all_failures,
        "evaluator_passed": report.passed,
        "failures": all_failures,
        "changed_files": list(report.changed_files),
        "observed": observed,
        "final_answer": report.observation.answer,
        "model_calls": report.observation.measurement.get("model_calls", 0),
        "tool_calls": report.observation.measurement.get("tool_calls", 0),
        "expected_final_answer": practical_final_answer(scenario.scenario_id),
    }


def run_practical_scripted(
    repo_root: str | Path,
    *,
    output_path: str | Path | None = None,
    scenarios: tuple[CapabilityScenario, ...] = PRACTICAL_V1,
) -> dict[str, Any]:
    """Run exactly one deterministic execution for each practical scenario."""

    root = Path(repo_root).resolve()
    ids = tuple(item.scenario_id for item in scenarios)
    if ids != tuple(f"PV1-{index:02d}" for index in range(1, 9)):
        raise ValueError("PRACTICAL-V1 exige exatamente PV1-01..PV1-08 em ordem")
    candidate_start = candidate_identity(root)
    model_identity = fake_model_identity()
    from agent.evaluation.model_identity import planned_model_profile

    runtime_profile = planned_model_profile(root)
    records: list[dict[str, Any]] = []
    for scenario in scenarios:
        captured: dict[str, ScriptedEvaluationGateway] = {}

        executor = AgentApplicationScenarioExecutor(
            _PracticalGatewayFactory(scenario, captured),
            approval_policy=AutoApprove(),
            prepare_workspace=lambda _objective, workspace, _scenario=scenario: prepare_practical_workspace(
                _scenario, workspace
            ),
            prepare_application=lambda _objective, workspace, paths, _scenario=scenario: prepare_practical_application(
                _scenario, workspace, paths
            ),
        )
        with tempfile.TemporaryDirectory(prefix=f"evaluation-{scenario.scenario_id.lower()}-") as raw_root:
            scenario_report = CapabilityEvaluator(executor).evaluate(
                scenario,
                Path(raw_root) / "workspace",
            )
        records.append(_scenario_record(
            scenario,
            scenario_report,
            _prompt_projection(captured.get("gateway")),
        ))
    candidate_end = candidate_identity(root)
    report = {
        "schema_version": 1,
        "practical_set_version": PRACTICAL_SET_VERSION,
        "practical_fixture_identity": practical_fixture_identity(scenarios),
        "evidence_level": EvidenceLevel.DETERMINISTIC.value,
        "candidate_start": candidate_start,
        "candidate_end": candidate_end,
        "candidate_start_identity": candidate_identity_string(candidate_start),
        "candidate_end_identity": candidate_identity_string(candidate_end),
        "candidate_unchanged": candidate_start == candidate_end,
        "semantic_manifest_hash": semantic_manifest_hash(semantic_candidate_manifest(root)),
        "model_identity": model_identity,
        "model_config_fingerprint": model_identity.get("model_config_fingerprint"),
        "runtime_profile_fingerprint": runtime_profile.get("runtime_profile_fingerprint"),
        "observed_model_identity": {
            "available": True,
            "provider_model_id": "scripted-evaluation",
            "source": "in-process scripted gateway",
        },
        "execution_policy": {
            "one_execution_per_scenario": True,
            "environmental_retry": False,
            "qwen_used": False,
        },
        "scenarios": records,
        "summary": {
            "total": len(records),
            "passed": sum(bool(item["passed"]) for item in records),
            "failed": sum(not bool(item["passed"]) for item in records),
            "unknown_failures": 0,
        },
    }
    safe_report = cast(dict[str, Any], sanitize_evidence(report))
    if output_path is not None:
        destination = Path(output_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        import json

        destination.write_text(
            json.dumps(safe_report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return safe_report


__all__ = ["run_practical_scripted"]

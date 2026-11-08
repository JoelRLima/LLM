"""One isolated task execution for the weak-model campaign."""

from __future__ import annotations

import tempfile
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Mapping

from llm_agent.agent.approval import AutoApprove
from llm_agent.agent.evaluation.agent_executor import AgentApplicationScenarioExecutor
from llm_agent.agent.evaluation.contracts import CapabilityScenario, EvaluationFailure
from llm_agent.agent.evaluation.oracle import deterministic_oracle_evidence
from llm_agent.agent.evaluation.practical_fixture_preparation import (
    prepare_practical_application,
    prepare_practical_workspace,
)
from llm_agent.agent.evaluation.runner import CapabilityEvaluator
from llm_agent.agent.evaluation.scenario_contracts import H_SERIES
from llm_agent.agent.evaluation.trace import RecordingGateway
from llm_agent.agent.evaluation.weak_model_data_oracles import grade_data_case
from llm_agent.agent.evaluation.weak_model_fixtures import WeakModelScriptedGateway
from llm_agent.agent.llm.contracts import ModelGateway
from llm_agent.agent.runtime.config_repository import ConfigRepository
from llm_agent.agent.tools.authority import TaskAuthoritySnapshot
from llm_agent.agent.variants.models import VariantComposition


@dataclass(frozen=True)
class CaseExecution:
    report: Any | None
    capability_passed: bool | None
    oracle_failures: tuple[EvaluationFailure, ...]
    wall_latency_ms: int
    error: str | None = None


CASE_BUDGET_CAPS = {
    "max_model_calls": 20,
    "max_task_steps": 30,
    "max_task_tokens": 200_000,
    "max_task_tool_calls": 30,
    "max_task_wall_seconds": 300,
}


def case_budgets(config: Mapping[str, Any] | None) -> dict[str, int]:
    """Use canonical runtime budgets, preserving stricter caller limits."""
    values = config or {}
    return {
        key: min(value, cap) if type(value := values.get(key)) is int and value > 0 else cap
        for key, cap in CASE_BUDGET_CAPS.items()
    }


def _install_config(paths: Any, config: Mapping[str, Any] | None, variant: str) -> None:
    repository = ConfigRepository(paths)
    if config:
        changes = dict(config)
        changes.pop("schema_version", None)
        # These legacy paths are derived from the isolated workspace and are
        # not accepted by the current standalone configuration schema.
        changes.pop("checkpoint_file", None)
        task_report = changes.get("task_report")
        if isinstance(task_report, dict):
            task_report.pop("output_dir", None)
        repository.update(changes)
    repository.update({"agent_execution_profile": variant, **case_budgets(config)})


def _executor(
    scenario: CapabilityScenario,
    variant: str,
    config: Mapping[str, Any] | None,
    live_gateway_factory: Callable[[str, Path], ModelGateway] | None,
) -> AgentApplicationScenarioExecutor:
    factory: Callable[[str, Path], Any]
    if live_gateway_factory is None:
        def factory(objective: str, _workspace: Path) -> Any:
            fixture_marker = scenario.metadata.get("fixture_marker", scenario.scenario_id)
            return RecordingGateway(
                WeakModelScriptedGateway(objective, fixture_marker=str(fixture_marker))
            )
    else:
        factory = live_gateway_factory

    def prepare_application(objective: str, workspace: Path, paths: Any) -> None:
        del objective
        prepare_practical_application(scenario, workspace, paths)
        _install_config(paths, config, variant)

    def prepare_workspace(objective: str, workspace: Path) -> None:
        del objective
        prepare_practical_workspace(scenario, workspace)

    authority_capabilities = scenario.metadata.get("task_authority_capabilities")
    if scenario.scenario_id == "PV1-05":
        authority = TaskAuthoritySnapshot(frozenset({"read"}))
    elif isinstance(authority_capabilities, (list, tuple, set, frozenset)):
        authority = TaskAuthoritySnapshot(frozenset(authority_capabilities))
    else:
        authority = None
    return AgentApplicationScenarioExecutor(
        factory,
        approval_policy=AutoApprove(),
        task_authority=authority,
        variant_composition=VariantComposition.production_current(),
        prepare_workspace=prepare_workspace,
        prepare_application=prepare_application,
    )


def execute_weak_model_case(
    scenario: CapabilityScenario,
    variant: str,
    *,
    live: bool,
    config: Mapping[str, Any] | None,
    gateway_factory: Callable[[str, Path], ModelGateway] | None,
) -> CaseExecution:
    """Execute one fixture with the canonical evaluator; keep errors attributable."""
    started = time.monotonic()
    try:
        executor = _executor(scenario, variant, config, gateway_factory if live else None)
        with tempfile.TemporaryDirectory(prefix=f"weak-model-{variant}-{scenario.scenario_id}-") as raw_root:
            workspace = Path(raw_root) / "workspace"
            report = CapabilityEvaluator(executor).evaluate(scenario, workspace)
            capability_passed = report.passed
            oracle_failures = _independent_oracle_failures(scenario, report, workspace)
            if oracle_failures:
                report = replace(report, passed=False, failures=(*report.failures, *oracle_failures))
        return CaseExecution(report, capability_passed, oracle_failures, _elapsed_ms(started))
    except Exception as exc:
        return CaseExecution(
            None, None, (), _elapsed_ms(started), f"{type(exc).__name__}: {exc}"
        )


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _independent_oracle_failures(
    scenario: CapabilityScenario, report: Any, workspace: Path,
) -> tuple[EvaluationFailure, ...]:
    if scenario.scenario_id.startswith("WM-"):
        return grade_data_case(
            scenario.scenario_id, workspace, scenario.initial_files, report.changed_files
        )
    h_id, arm_id = scenario.metadata.get("h_id"), scenario.metadata.get("arm_id")
    if not isinstance(h_id, str) or not isinstance(arm_id, str):
        return ()
    source = next((item for item in H_SERIES if item.h_id == h_id), None)
    arm = next((item for item in source.arms if item.arm_id == arm_id), None) if source else None
    if arm is None:
        return (EvaluationFailure("weak_oracle_definition_missing", "H-series oracle arm is missing."),)
    evidence = deterministic_oracle_evidence(report, arm)
    return tuple(
        EvaluationFailure(f"h_series_oracle:{code}", "H-series deterministic oracle failed.")
        for code in evidence.get("failures", ())
    )


__all__ = ["CaseExecution", "case_budgets", "execute_weak_model_case"]

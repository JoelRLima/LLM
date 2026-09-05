"""Scenario-specific deterministic oracle dispatch for PRACTICAL-V1."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent.evaluation.contracts import CapabilityScenario
from agent.evaluation.practical_projection_support import (
    _code_projection,
    _oracle_pv1_01,
    _oracle_pv1_02,
    _oracle_pv1_03,
    _oracle_pv1_04,
    _oracle_pv1_05,
    _oracle_pv1_06,
    _oracle_pv1_07,
    _oracle_pv1_08,
    _repository_projection,
    _validation_projection,
)


def _oracle_failures(
    scenario: CapabilityScenario,
    report: Any,
    *,
    prompt: Mapping[str, Any],
) -> tuple[list[str], dict[str, Any]]:
    observation = report.observation
    code = _code_projection(observation)
    validation = _validation_projection(observation)
    repository = _repository_projection(observation)
    observed: dict[str, Any] = {
        "task_status": observation.measurement.get("status"),
        "failure_code": observation.error,
        "changed_files": list(report.changed_files),
        "code": code,
        "validation": validation,
        "repository": repository,
        "prompt_projection": dict(prompt),
    }
    failures: list[str] = []
    if not observation.success and scenario.expectation.success:
        failures.append("unexpected_non_success")
    checks = {
        "PV1-01": lambda: _oracle_pv1_01(code, report),
        "PV1-02": lambda: _oracle_pv1_02(code, report),
        "PV1-03": lambda: _oracle_pv1_03(code, observation, report),
        "PV1-04": lambda: _oracle_pv1_04(observation, report),
        "PV1-05": lambda: _oracle_pv1_05(observation, report, prompt),
        "PV1-06": lambda: _oracle_pv1_06(report, prompt),
        "PV1-07": lambda: _oracle_pv1_07(repository, report),
        "PV1-08": lambda: _oracle_pv1_08(validation, report),
    }
    check = checks.get(scenario.scenario_id)
    if check is not None:
        failures.extend(check())
    oracle = scenario.metadata.get("oracle", {})
    return list(dict.fromkeys(failures)), observed | {"oracle_contract": oracle}


__all__ = ["_oracle_failures"]

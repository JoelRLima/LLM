from __future__ import annotations

import json
from pathlib import Path

from agent.evaluation import (
    CapabilityEvaluator,
    CapabilityScenario,
    EvaluationSetReport,
    ExecutionObservation,
    ScenarioExpectation,
)
from agent.evaluation.regressions import CURATED_REGRESSION_SET


class StaticExecutor:
    def __init__(self, *, success: bool = True, answer: str = "ok") -> None:
        self.success = success
        self.answer = answer

    def execute(self, objective: str, workspace: Path) -> ExecutionObservation:
        del objective
        return ExecutionObservation(
            success=self.success,
            answer=self.answer,
            measurement={"task_id": "test", "status": "succeeded" if self.success else "failed"},
        )


def test_set_aggregates_exports_and_keeps_expected_observed_outcomes(tmp_path: Path) -> None:
    scenarios = (
        CapabilityScenario("pass", "read", "read", expectation=ScenarioExpectation(answer_contains=("ok",))),
        CapabilityScenario("fail", "read", "read", expectation=ScenarioExpectation(answer_contains=("missing",))),
    )

    report = CapabilityEvaluator(StaticExecutor()).evaluate_set(scenarios, tmp_path / "set")

    assert isinstance(report, EvaluationSetReport)
    assert (report.total, report.passed, report.failed) == (2, 1, 1)
    exported = report.to_dict()
    assert exported["summary"] == {
        "total": 2,
        "passed": 1,
        "failed": 1,
        "pass_rate": 0.5,
        "failures_by_category": {"read": 1},
    }
    assert exported["tasks"][1]["expected"]["success"] is True
    assert exported["tasks"][1]["passed"] is False
    assert "missing" in exported["tasks"][1]["reason"]

    destination = tmp_path / "reports" / "eval.json"
    report.write_json(destination)
    assert json.loads(destination.read_text(encoding="utf-8")) == exported


def test_set_requires_empty_root_to_prevent_cross_task_contamination(tmp_path: Path) -> None:
    root = tmp_path / "set"
    root.mkdir()
    (root / "foreign.txt").write_text("foreign", encoding="utf-8")
    scenario = CapabilityScenario("one", "read", "read")

    try:
        CapabilityEvaluator(StaticExecutor()).evaluate_set((scenario,), root)
    except ValueError as exc:
        assert "raiz" in str(exc)
    else:
        raise AssertionError("non-empty evaluation root was accepted")


def test_negative_harness_proof_fails_when_observed_outcome_is_wrong(tmp_path: Path) -> None:
    scenario = CapabilityScenario(
        "expected-failure",
        "failure",
        "failure",
        expectation=ScenarioExpectation(success=False),
    )

    report = CapabilityEvaluator(StaticExecutor(success=True)).evaluate(scenario, tmp_path)

    assert report.passed is False
    assert {failure.code for failure in report.failures} == {"unexpected_success"}


def test_curated_regression_set_is_focused_and_excludes_historical_ci_debt() -> None:
    historical = {
        "test_invalid_entrypoint_uses_canonical_manifest_invalid_diagnostic",
        "test_cleanup_failure_does_not_mask_primary_error",
        "test_context_preserves_body_error_when_release_also_fails",
    }

    assert {case.category for case in CURATED_REGRESSION_SET} == {
        "R-AUTHORITY", "R-TERMINALITY", "R-PROCESS", "R-WRITER",
        "R-SHELL-GIT", "R-STDIO", "R-INSTALLED", "R-MEASUREMENT",
    }
    assert all(not any(name in case.pytest_node for name in historical) for case in CURATED_REGRESSION_SET)
    assert all(Path(case.pytest_node.split("::", 1)[0]).is_file() for case in CURATED_REGRESSION_SET)

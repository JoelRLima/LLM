from copy import deepcopy
from types import SimpleNamespace

import pytest

from llm_agent.agent.evaluation.weak_model_gate import build_evaluation_gate


def _row():
    return {
        "attempted": True, "status": "completed", "passed": True,
        "configured_model_alias": "local-small",
        "observed_provider_identity": {
            "actual_provider_model_id": "concrete-small-model-v1", "complete": True, "consistent": True,
            "source": "response.provider_metadata",
        },
        "oracle_checks": {"definition_valid": True, "independent_oracle": "passed"},
    }


def _gate(rows, **kwargs):
    return build_evaluation_gate(rows, live=True, source_unchanged=True, **kwargs)


def test_gate_requires_every_case_to_pass():
    rows = [_row() for _ in range(22)]
    assert _gate(rows)["status"] == "passed"
    rows[-1]["passed"] = False
    assert _gate(rows)["status"] == "failed"
    assert _gate(rows)["failed_cases"] == 1


@pytest.mark.parametrize("identity", ["default", "unknown", "", "scripted-evaluation"])
def test_generic_observed_model_cannot_pass(identity):
    row = _row()
    row["observed_provider_identity"]["actual_provider_model_id"] = identity
    assert _gate([row])["status"] == "inconclusive"


def test_missing_oracle_is_distinct_from_failed_oracle():
    row = _row()
    row["oracle_checks"]["definition_valid"] = False
    assert _gate([row])["status"] == "inconclusive"
    row["oracle_checks"] = {"definition_valid": True, "independent_oracle": "failed"}
    row["passed"] = False
    assert _gate([row])["status"] == "failed"


def test_source_changes_or_unrun_cases_cannot_pass():
    assert build_evaluation_gate([_row()], live=True, source_unchanged=False)["status"] == "inconclusive"
    assert _gate([_row(), {"attempted": False}])["status"] == "inconclusive"
    assert build_evaluation_gate([_row()], live=False, source_unchanged=True)["status"] == "inconclusive"


def test_case_budget_caps_preserve_stricter_user_limits():
    from llm_agent.agent.evaluation.weak_model_execution import case_budgets

    budgets = case_budgets({"max_model_calls": 4, "max_task_wall_seconds": 9_999})
    assert budgets["max_model_calls"] == 4
    assert budgets["max_task_wall_seconds"] == 300
    assert budgets["max_task_tool_calls"] == 30


def test_full_invocation_history_detects_safety_failure_before_clipping():
    from llm_agent.agent.evaluation.weak_model_campaign_rows import observation_row
    from llm_agent.agent.evaluation.weak_model_fixtures import WEAK_MODEL_FIXTURES

    scenario = next(s for s in WEAK_MODEL_FIXTURES if s.scenario_id == "h1-direct-no-tool")
    invocations = [{"tool": "file_reader"}] + [{"tool": "allowed"}] * 12
    report = SimpleNamespace(
        scenario_id=scenario.scenario_id, passed=False, failures=(), changed_files=(),
        observation=SimpleNamespace(
            success=False, answer="", error=None, diagnostics=[], measurement={},
            evidence={"invocation_evidence": invocations},
        ),
    )
    row = observation_row(report, scenario, variant="standard", repetition=1, live=True,
                          independent_oracle_failures=(), capability_evaluator_passed=False)
    assert len(row["tool_evidence"]) == 8
    assert row["unauthorized_effect"] is True
    assert row["safety_violation"] is True
    assert _gate([row])["status"] == "failed"


def test_live_safety_stop_preserves_unrun_cases(monkeypatch):
    import llm_agent.agent.evaluation.weak_model_campaign as campaign
    from llm_agent.agent.evaluation.weak_model_fixtures import WEAK_MODEL_FIXTURES

    calls = []

    def execute(scenario, *args, **kwargs):
        calls.append(scenario.scenario_id)
        return SimpleNamespace(report=object(), oracle_failures=(), capability_passed=False, wall_latency_ms=0)

    def observation(*args, **kwargs):
        row = deepcopy(_row())
        row.update(variant=kwargs["variant"], repetition=kwargs["repetition"],
                   scenario_id=calls[-1], passed=False, false_success=True, safety_violation=True)
        return row

    monkeypatch.setattr(campaign, "execute_weak_model_case", execute)
    monkeypatch.setattr(campaign, "observation_row", observation)
    selected = tuple(s.scenario_id for s in WEAK_MODEL_FIXTURES[:5])
    report = campaign.run_weak_model_campaign(
        ".", variants=("standard",), live=True, gateway_factory=lambda *a: None,
        configured_model_alias="local-small", case_ids=selected,
    )
    assert calls == [selected[0]]
    assert len(report["results"]) == 5
    assert report["evaluation_gate"]["status"] == "failed"
    assert report["evaluation_gate"]["unrun_cases"] == 4
    assert report["summary"]["standard"]["not_run_cases"] == 4


@pytest.mark.parametrize(("live", "gate", "expected"), [
    (True, "inconclusive", 2), (True, "failed", 1), (True, "passed", 0),
    (False, "inconclusive", 0),
])
def test_cli_does_not_report_live_inconclusive_as_success(monkeypatch, tmp_path, live, gate, expected):
    from scripts import run_weak_model_campaign as cli

    monkeypatch.setattr(cli, "run_weak_model_campaign", lambda *args, **kwargs: {
        "results": [{"passed": True}], "comparison_valid": True,
        "model_used": False, "evaluation_gate": {"status": gate},
    })
    arguments = ["--variant", "standard"]
    if live:
        config = tmp_path / "config.json"
        config.write_text("{}", encoding="utf-8")
        arguments += ["--live", "--config", str(config)]
    assert cli.main(arguments) == expected

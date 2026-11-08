from __future__ import annotations

from types import SimpleNamespace

import pytest

from llm_agent.agent.evaluation.weak_model_campaign import run_weak_model_campaign
from llm_agent.agent.evaluation.weak_model_fixtures import (
    WEAK_MODEL_FIXTURES,
    fixture_fingerprint,
)


def test_fixture_set_has_independent_oracles_and_stable_fingerprint() -> None:
    assert len(WEAK_MODEL_FIXTURES) == 22
    assert {item.scenario_id for item in WEAK_MODEL_FIXTURES} >= {
        "PV1-01", "PV1-02", "PV1-03", "PV1-04", "PV1-05", "PV1-06", "PV1-08",
        "r1-h2-scalar", "r10-h10-false", "h1-direct-no-tool", "h11-partial-failure", "WM-JSON", "WM-CSV", "WM-REPORT"
    }
    assert len(fixture_fingerprint()) == 64
    assert all(item.metadata["oracle"] for item in WEAK_MODEL_FIXTURES)
    classes = {item.metadata["task_class"] for item in WEAK_MODEL_FIXTURES}
    assert {
        "missing_arguments_need_clarification", "direct_answer_no_irrelevant_tool",
        "tool_failure_partial_progress", "tool_failure_no_effect_wrong_final_success",
        "fresh_read_over_stale_context", "multi_step_result_binding",
        "multi_step_continuation", "unauthorized_scope",
    } <= classes


def test_campaign_rejects_invalid_variant_and_live_without_gateway() -> None:
    with pytest.raises(ValueError, match="variants"):
        run_weak_model_campaign(".", variants=("guess",))
    with pytest.raises(ValueError, match="gateway_factory"):
        run_weak_model_campaign(".", live=True)
    with pytest.raises(ValueError, match="repetitions"):
        run_weak_model_campaign(".", repetitions=1.5)  # type: ignore[arg-type]


def _fake_case_execution(scenario):
    from llm_agent.agent.evaluation.weak_model_execution import CaseExecution

    report = SimpleNamespace(
        scenario_id=scenario.scenario_id,
        observation=SimpleNamespace(
            success=True, answer="ok", error=None, diagnostics=[], measurement={}, evidence={}
        ),
        passed=True,
        failures=(),
        changed_files=(),
    )
    return CaseExecution(report, True, (), 0)


def test_campaign_orders_variants_and_labels_fixture_backend(monkeypatch) -> None:
    import llm_agent.agent.evaluation.weak_model_campaign as campaign
    monkeypatch.setattr(
        campaign, "execute_weak_model_case",
        lambda scenario, _variant, **_kwargs: _fake_case_execution(scenario),
    )
    report = run_weak_model_campaign(".", variants=("standard", "compact"), repetitions=2)
    assert report["model_used"] is False
    assert report["backend"] == "deterministic-fixture"
    assert report["live_model_response_observed"] is False
    assert report["fixture_results_predict_model_accuracy"] is False
    assert report["variant_order"] == ["standard", "compact", "compact", "standard"]
    assert len(report["results"]) == 88
    assert report["fixture_version"] == "weak-model-v2"
    assert report["evaluation_gate"]["status"] == "inconclusive"
    assert report["evaluation_gate"]["attempted_cases"] == 88
    assert report["evaluation_gate"]["failed_cases"] == 0
    assert report["comparison_valid"] is True


def test_source_change_invalidates_campaign_comparison(monkeypatch) -> None:
    import llm_agent.agent.evaluation.weak_model_campaign as campaign

    fingerprints = iter(("before", "after"))
    monkeypatch.setattr(campaign, "_source_fingerprint", lambda _root: next(fingerprints))
    monkeypatch.setattr(
        campaign, "execute_weak_model_case",
        lambda scenario, _variant, **_kwargs: _fake_case_execution(scenario),
    )
    report = run_weak_model_campaign(".", variants=("standard",))
    assert report["source_unchanged"] is False
    assert report["comparison_valid"] is False


def test_canary_selection_is_bounded_and_preserved_in_report(monkeypatch) -> None:
    import llm_agent.agent.evaluation.weak_model_campaign as campaign
    monkeypatch.setattr(
        campaign, "execute_weak_model_case",
        lambda scenario, _variant, **_kwargs: _fake_case_execution(scenario),
    )
    selected = tuple(item.scenario_id for item in WEAK_MODEL_FIXTURES[:5])
    report = run_weak_model_campaign(".", variants=("standard",), case_ids=selected)
    assert report["selected_case_ids"] == list(selected)
    assert len(report["results"]) == 5
    with pytest.raises(ValueError, match="5 to 10"):
        run_weak_model_campaign(".", variants=("standard",), case_ids=selected[:4])


def test_live_executor_receives_selected_profile_without_network(monkeypatch) -> None:
    import llm_agent.agent.evaluation.weak_model_execution as execution

    config_updates = []
    gateway_calls = []
    fake_gateway = object()

    class Config:
        def __init__(self, _paths):
            pass

        def update(self, changes):
            config_updates.append(dict(changes))

    class Executor:
        def __init__(self, gateway_factory, **kwargs):
            self.gateway_factory = gateway_factory
            self.prepare_application = kwargs["prepare_application"]
            self.prepare_workspace = kwargs["prepare_workspace"]

    class Evaluator:
        def __init__(self, executor):
            self.executor = executor

        def evaluate(self, scenario, workspace):
            self.executor.prepare_workspace(scenario.objective, workspace)
            self.executor.prepare_application(scenario.objective, workspace, object())
            assert self.executor.gateway_factory(scenario.objective, workspace) is fake_gateway
            return SimpleNamespace(
                scenario_id=scenario.scenario_id,
                observation=SimpleNamespace(
                    success=True, answer="ok", error=None, diagnostics=[], measurement={}, evidence={}
                ),
                passed=True, failures=(), changed_files=(),
            )

    def selected_gateway(objective, workspace):
        gateway_calls.append((objective, workspace))
        return fake_gateway

    class TemporaryDirectory:
        def __init__(self, **_kwargs):
            self.name = "."

        def __enter__(self):
            return self.name

        def __exit__(self, *_args):
            return None

    monkeypatch.setattr(execution, "ConfigRepository", Config)
    monkeypatch.setattr(execution, "AgentApplicationScenarioExecutor", Executor)
    monkeypatch.setattr(execution, "CapabilityEvaluator", Evaluator)
    monkeypatch.setattr(execution.tempfile, "TemporaryDirectory", TemporaryDirectory)
    monkeypatch.setattr(execution, "grade_data_case", lambda *_args: ())
    selected_config = {
        "schema_version": 1,
        "default_model_profile": "weak-small",
        "model_profiles": {"weak-small": {"model": "verified-profile"}},
    }
    result = execution.execute_weak_model_case(
        WEAK_MODEL_FIXTURES[0],
        "compact",
        live=True,
        config=selected_config,
        gateway_factory=selected_gateway,
    )
    assert result.report is not None
    assert config_updates[0]["default_model_profile"] == "weak-small"
    assert config_updates[1]["agent_execution_profile"] == "compact"
    assert config_updates[1]["max_task_wall_seconds"] == 300
    assert config_updates[1]["max_model_calls"] == 20
    assert gateway_calls and gateway_calls[0][0] == WEAK_MODEL_FIXTURES[0].objective

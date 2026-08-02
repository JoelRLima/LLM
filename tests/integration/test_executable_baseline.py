from pathlib import Path

import pytest

from agent.evaluation import CapabilityEvaluator, load_scenario
from tests.support.offline_scenarios import OfflineScenarioExecutor

CAPABILITY_SCENARIOS = Path(__file__).parents[1] / "fixtures" / "capabilities"
JOURNEY_SCENARIOS = Path(__file__).parents[1] / "fixtures" / "journeys"


def _scenario_files(root: Path) -> list[Path]:
    return sorted(root.glob("*.json"))


@pytest.mark.parametrize(
    "scenario_path",
    _scenario_files(CAPABILITY_SCENARIOS),
    ids=lambda path: path.stem,
)
def test_code_capability_baseline_runs_real_code_task_entrypoint(
    scenario_path: Path,
    tmp_path: Path,
) -> None:
    scenario = load_scenario(scenario_path)
    executor = OfflineScenarioExecutor(scenario)

    report = CapabilityEvaluator(executor).evaluate(scenario, tmp_path)

    assert report.passed, [failure.message for failure in report.failures]
    expected_calls = scenario.metadata["execution"].get("expected_model_calls")
    if expected_calls is not None:
        assert executor.model_calls == expected_calls


@pytest.mark.parametrize(
    "scenario_path",
    _scenario_files(JOURNEY_SCENARIOS),
    ids=lambda path: path.stem,
)
def test_chat_and_builtin_skill_journeys_are_offline_and_observable(
    scenario_path: Path,
    tmp_path: Path,
) -> None:
    scenario = load_scenario(scenario_path)

    report = CapabilityEvaluator(OfflineScenarioExecutor(scenario)).evaluate(
        scenario,
        tmp_path,
    )

    assert report.passed, [failure.message for failure in report.failures]

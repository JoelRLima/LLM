from pathlib import Path

import pytest

from agent.evaluation import CapabilityEvaluator, ExecutionObservation, load_scenario, load_scenarios

SCENARIOS = Path(__file__).parents[1] / "fixtures" / "capabilities"


class ScriptedExecutor:
    def __init__(self, action, *, success=True, answer="", steps=1):
        self.action = action
        self.success = success
        self.answer = answer
        self.steps = steps

    def execute(self, objective: str, workspace: Path) -> ExecutionObservation:
        self.action(workspace)
        return ExecutionObservation(success=self.success, answer=self.answer, steps=self.steps)


def test_loads_six_baseline_capability_scenarios():
    scenarios = load_scenarios(SCENARIOS)

    assert len(scenarios) == 6
    assert {scenario.capability for scenario in scenarios} == {
        "analyze",
        "generate",
        "modify",
        "repair",
        "review",
        "multitask",
    }


def test_evaluator_checks_real_files_and_answer(tmp_path: Path):
    scenario = load_scenario(SCENARIOS / "02_generate.json")

    def generate(workspace: Path) -> None:
        (workspace / "math_utils.py").write_text(
            "def add(a: int, b: int) -> int:\n    return a + b\n",
            encoding="utf-8",
        )
        tests = workspace / "tests"
        tests.mkdir()
        (tests / "test_math_utils.py").write_text("def test_add():\n    assert True\n", encoding="utf-8")

    report = CapabilityEvaluator(
        ScriptedExecutor(generate, answer="Implementação validada", steps=4)
    ).evaluate(scenario, tmp_path)

    assert report.passed is True
    assert report.changed_files == ("math_utils.py", "tests/test_math_utils.py")


def test_evaluator_does_not_accept_convincing_answer_without_effect(tmp_path: Path):
    scenario = load_scenario(SCENARIOS / "02_generate.json")
    report = CapabilityEvaluator(
        ScriptedExecutor(lambda _workspace: None, answer="Tudo implementado e validado", steps=1)
    ).evaluate(scenario, tmp_path)

    assert report.passed is False
    assert {failure.code for failure in report.failures} >= {"file_existence"}


def test_evaluator_rejects_changes_outside_allowlist(tmp_path: Path):
    scenario = load_scenario(SCENARIOS / "05_review.json")

    def mutate(workspace: Path) -> None:
        (workspace / "service.py").write_text("def load():\n    return 'mutated'\n", encoding="utf-8")

    report = CapabilityEvaluator(
        ScriptedExecutor(mutate, answer="Uso de eval na linha 2", steps=2)
    ).evaluate(scenario, tmp_path)

    assert report.passed is False
    assert "file_changed" in {failure.code for failure in report.failures}


def test_evaluator_rejects_non_empty_workspace(tmp_path: Path):
    (tmp_path / "foreign.txt").write_text("x", encoding="utf-8")
    scenario = load_scenario(SCENARIOS / "01_analyze.json")

    with pytest.raises(ValueError, match="deve estar vazio"):
        CapabilityEvaluator(ScriptedExecutor(lambda _workspace: None)).evaluate(scenario, tmp_path)

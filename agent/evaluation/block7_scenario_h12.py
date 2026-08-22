"""H12 code-task scenario definition."""

from __future__ import annotations

from agent.evaluation.block7 import HSeriesArm, HSeriesScenario
from agent.evaluation.contracts import FileExpectation, ScenarioExpectation


def h12_scenario() -> HSeriesScenario:
    return HSeriesScenario(
        "H12",
        "code modify validate rollback truth",
        "code-task-validation-rollback",
        (
            HSeriesArm(
                "modify-validate",
                "H12: altere h12_module.py para retornar 2 e valide a modificação localmente.",
                initial_files={"h12_module.py": "def value():\n    return 1\n"},
                expectation=ScenarioExpectation(
                    files=(
                        FileExpectation("h12_module.py", contains=("return 2",), not_contains=("return 1",)),
                    ),
                    answer_contains=("valid",),
                    max_steps=5,
                ),
                oracle={
                    "required_tools": ("code_task",),
                    "required_validation": "passed",
                    "rollback_must_be_false": True,
                },
            ),
        ),
        3,
    )


__all__ = ["h12_scenario"]

from types import SimpleNamespace

from agent.planning.hierarchical_executor import HierarchicalExecutor
from agent.planning.semantic_projection import project_outcomes
from agent.planning.step_contracts import StepExecutionOutcome, StepOutcomeKind


def test_projection_uses_first_decisive_logical_slot() -> None:
    later_terminal = StepExecutionOutcome(
        StepOutcomeKind.BLOCKED, result={"ok": False, "status": "blocked"}
    )
    earlier_replan = StepExecutionOutcome(
        StepOutcomeKind.REPLAN, result={"ok": False, "status": "failed"}
    )

    projection = project_outcomes(
        [
            (4, later_terminal, later_terminal.result or {}),
            (2, earlier_replan, earlier_replan.result or {}),
        ]
    )

    assert projection is not None
    assert projection.logical_slot == 2
    assert projection.outcome.kind is StepOutcomeKind.REPLAN


def test_hierarchical_consumer_uses_projection_not_last_history_record() -> None:
    projection = SimpleNamespace(
        result={"ok": False, "status": "blocked"}, decisive=True
    )
    history = [
        {"result": {"ok": False, "status": "blocked"}},
        {"result": {"ok": True, "status": "succeeded"}},
    ]

    assert not HierarchicalExecutor._determine_step_success(history, projection)


def test_hierarchical_consumer_preserves_successful_final_projection() -> None:
    projection = SimpleNamespace(
        result={"ok": True, "status": "succeeded"},
        outcome=SimpleNamespace(kind=StepOutcomeKind.FINAL),
        decisive=True,
    )

    assert HierarchicalExecutor._determine_step_success(
        [{"result": {"ok": True}}], projection
    )

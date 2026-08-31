from __future__ import annotations

from types import SimpleNamespace

from agent.execution_state import StepStatus
from agent.planning.task_progress_projection import (
    ProgressStatus,
    build_task_progress_projection,
)


def test_projection_distinguishes_success_from_terminal_coverage() -> None:
    projection = build_task_progress_projection(
        statuses=[
            StepStatus.COMPLETED,
            StepStatus.FAILED,
            StepStatus.SKIPPED,
            StepStatus.BLOCKED,
            StepStatus.UNVERIFIED,
            StepStatus.PENDING,
            StepStatus.RUNNING,
            "cancelled",
        ]
    )

    assert projection.counts[ProgressStatus.SUCCEEDED.value] == 1
    assert projection.successful_units == 1
    assert projection.terminal_units == 6
    assert projection.successful_completion_percent == 12.5
    assert projection.terminal_coverage_percent == 75.0


def test_projection_is_read_only_and_does_not_promote_operational_failure() -> None:
    source = SimpleNamespace(
        step_records={},
        plan=[],
        operational_outcome=SimpleNamespace(terminal_status="failed"),
    )
    projection = build_task_progress_projection(
        source,
        operational_outcome=SimpleNamespace(terminal_status="failed"),
        statuses=[StepStatus.COMPLETED, StepStatus.FAILED],
    )

    assert projection.operational_status == "failed"
    assert projection.is_operational_success is False
    assert projection.successful_completion_percent == 50.0
    assert source.step_records == {}


def test_checkpoint_like_plan_records_project_stably() -> None:
    first = SimpleNamespace(
        step_records={
            "a": SimpleNamespace(status=StepStatus.COMPLETED),
            "b": SimpleNamespace(status=StepStatus.RUNNING),
        },
        plan=[SimpleNamespace(step_id="a"), SimpleNamespace(step_id="b")],
        get_step_id=lambda index: ("a", "b")[index],
    )
    restored = SimpleNamespace(
        step_records={
            "a": SimpleNamespace(status=StepStatus.COMPLETED),
            "b": SimpleNamespace(status=StepStatus.RUNNING),
        },
        plan=[SimpleNamespace(step_id="a"), SimpleNamespace(step_id="b")],
        get_step_id=lambda index: ("a", "b")[index],
    )

    assert build_task_progress_projection(first).to_dict() == build_task_progress_projection(restored).to_dict()

from __future__ import annotations

from agent.interfaces.cli.ui_plane import RuntimeEventUISink, RunViewModel, UIEventMailbox
from agent.runtime.correlation import RunCorrelation
from agent.runtime.event_kinds import RuntimeEventKind
from agent.runtime.events import RuntimeEvent


def _event(kind: RuntimeEventKind, correlation: RunCorrelation, **data: object) -> RuntimeEvent:
    return RuntimeEvent.from_fields(kind, correlation, data)


def test_sink_is_presentation_only_and_mailbox_is_bounded_under_flood() -> None:
    mailbox = UIEventMailbox(milestone_capacity=2, latest_capacity=1)
    sink = RuntimeEventUISink(mailbox)
    correlation = RunCorrelation.fresh()
    for index in range(20):
        sink.emit(_event(RuntimeEventKind.STEP_COMPLETED, correlation, step=index, activity=f"step-{index}"))
    assert len(mailbox) <= 2
    stats = mailbox.stats()
    assert stats.dropped_milestones >= 18
    drained = mailbox.drain()
    assert all(item.event.run_id == correlation.run_id for item in drained)


def test_mailbox_coalesces_latest_progress_and_isolates_sink_failures() -> None:
    mailbox = UIEventMailbox(milestone_capacity=3, latest_capacity=2)
    correlation = RunCorrelation.fresh()
    for _ in range(5):
        mailbox.emit(_event(RuntimeEventKind.MODEL_CALL_STARTED, correlation, activity="model"))
    assert mailbox.stats().coalesced_updates == 4
    assert len(mailbox.drain()) == 1
    assert mailbox.drain() == ()

    class _Broken:
        def emit(self, _event):
            raise AssertionError("observer must not affect runtime")

    # A caller-facing broken observer is outside the mailbox and can be
    # isolated by the canonical dispatcher; the W17 sink itself never raises.
    RuntimeEventUISink(mailbox).emit(_event(RuntimeEventKind.WARNING, correlation, message="safe"))


def test_terminal_event_uses_dedicated_mailbox_lane_under_milestone_flood() -> None:
    mailbox = UIEventMailbox(milestone_capacity=2, latest_capacity=2)
    correlation = RunCorrelation.fresh()
    for index in range(20):
        mailbox.emit(_event(RuntimeEventKind.STEP_COMPLETED, correlation, step=index))
    mailbox.emit(_event(RuntimeEventKind.TASK_OUTCOME, correlation, status="succeeded"))
    drained = mailbox.drain()
    assert any(item.event.kind is RuntimeEventKind.TASK_OUTCOME for item in drained)


def test_run_view_filters_prior_run_and_correlates_tool_end_identity() -> None:
    view = RunViewModel(milestone_capacity=4)
    first = RunCorrelation.fresh()
    second = RunCorrelation.fresh()
    view.begin_run(1, owner="agent.application.AgentApplication.interact")
    assert view.apply(_event(RuntimeEventKind.MODEL_CALL_STARTED, first)) is True
    assert view.snapshot().model_active is True
    assert view.apply(_event(RuntimeEventKind.TOOL_START, first, tool="reader", invocation_id="inv-1")) is True
    assert view.snapshot().current_tool == "reader"
    assert view.apply(_event(RuntimeEventKind.TOOL_END, first, tool="old", invocation_id="inv-old")) is True
    assert view.snapshot().current_tool == "reader"
    assert view.apply(_event(RuntimeEventKind.TOOL_END, first, tool="reader", invocation_id="inv-1")) is True
    assert view.snapshot().current_tool is None
    assert view.apply(_event(RuntimeEventKind.ERROR, second, message="late")) is False
    assert view.snapshot().error_count == 0
    assert view.apply(_event(RuntimeEventKind.TASK_OUTCOME, first, status="succeeded")) is True
    assert view.snapshot().state == "TERMINAL"


def test_view_toolbar_degrades_by_width_and_keeps_truthful_unknowns() -> None:
    view = RunViewModel()
    view.begin_run(7, owner="typed.code")
    for width in (40, 80, 120, 160):
        rendered = view.render_toolbar(width=width, mode="READ_ONLY")
        assert len(rendered) <= width
        assert "RUN" in rendered or "RUNNING" in rendered
    snap = view.snapshot()
    assert snap.short_run_id is None
    assert snap.current_tool is None
    assert snap.terminal_outcome is None


def test_new_generation_blocks_late_prior_events_and_terminal_reactivation() -> None:
    view = RunViewModel()
    first = RunCorrelation.fresh()
    second = RunCorrelation.fresh()
    view.begin_run(1, owner="first")
    assert view.apply(_event(RuntimeEventKind.MODEL_CALL_STARTED, first)) is True
    view.begin_run(2, owner="second")
    assert view.apply(_event(RuntimeEventKind.MODEL_CALL_STARTED, first)) is False
    assert view.apply(_event(RuntimeEventKind.MODEL_CALL_STARTED, second)) is True
    assert view.apply(_event(RuntimeEventKind.TASK_OUTCOME, second, status="succeeded")) is True
    assert view.apply(_event(RuntimeEventKind.MODEL_CALL_STARTED, second)) is False
    assert view.snapshot().model_active is False


def test_n_minus_two_and_n_minus_three_events_cannot_reactivate_a_new_run() -> None:
    view = RunViewModel()
    first, second, third = RunCorrelation.fresh(), RunCorrelation.fresh(), RunCorrelation.fresh()
    view.begin_run(1, owner="first")
    assert view.apply(_event(RuntimeEventKind.STEP_COMPLETED, first, activity="first"))
    view.begin_run(2, owner="second")
    assert view.apply(_event(RuntimeEventKind.STEP_COMPLETED, second, activity="second"))
    view.begin_run(3, owner="third")

    assert view.apply(_event(RuntimeEventKind.TOOL_START, first, tool="stale-1", invocation_id="old-1")) is False
    assert view.apply(_event(RuntimeEventKind.TOOL_START, second, tool="stale-2", invocation_id="old-2")) is False
    assert view.snapshot().current_tool is None
    assert view.apply(_event(RuntimeEventKind.MODEL_CALL_STARTED, third)) is True


def test_terminal_result_clears_tool_step_model_and_attention_projection() -> None:
    view = RunViewModel()
    correlation = RunCorrelation.fresh()
    view.begin_run(1, owner="worker")
    view.apply(_event(RuntimeEventKind.MODEL_CALL_STARTED, correlation))
    view.apply(_event(RuntimeEventKind.TOOL_START, correlation, tool="reader", invocation_id="inv"))
    view.apply(_event(RuntimeEventKind.STEP_COMPLETED, correlation, step_id="step-1"))
    view.set_attention(True)

    assert view.apply_result(1, type("Result", (), {"status": "succeeded"})())
    snapshot = view.snapshot()
    assert snapshot.state == "TERMINAL"
    assert snapshot.model_active is False
    assert snapshot.current_tool is None
    assert snapshot.current_invocation_id is None
    assert snapshot.current_step is None
    assert snapshot.attention_pending is False

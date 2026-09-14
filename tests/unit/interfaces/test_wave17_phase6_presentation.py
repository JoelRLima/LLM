from __future__ import annotations

import sys
import time
from threading import Event, Thread
from types import SimpleNamespace

from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from agent.interfaces.cli import command_handlers, interactive_rendering
from agent.interfaces.cli.controller import InteractiveExecutionController, SubmissionEnvelope
from agent.interfaces.cli.interactive_shell import InteractiveShell
from agent.interfaces.cli.interactive_worker import InteractiveWorkerResult, execute_submission
from agent.interfaces.cli.manifest import DEFAULT_COMMAND_REGISTRY
from agent.interfaces.cli.ui_plane import RunViewModel
from agent.interfaces.cli.worker_stream import BoundedWorkerStream
from agent.runtime.correlation import RunCorrelation
from agent.runtime.event_kinds import RuntimeEventKind
from agent.runtime.events import RuntimeEvent
from agent.runtime.worker_output import emit_worker_output


def test_debug_level_in_interactive_context_never_enables_worker_verbose_prints(monkeypatch) -> None:
    output: list[str] = []
    monkeypatch.setattr(command_handlers, "console", SimpleNamespace(print=lambda value, **_: output.append(str(value))))
    orchestrator = SimpleNamespace(verbose=False, context_manager=SimpleNamespace(verbose=False))
    ctx = SimpleNamespace(orchestrator=orchestrator, controller=object(), modo_diagnostico=0)
    command_handlers.toggle_debug("/debug", ctx)
    command_handlers.toggle_debug("/debug", ctx)
    assert ctx.diagnostic_level == "VERBOSE"
    assert orchestrator.verbose is False
    assert orchestrator.context_manager.verbose is False


def test_view_remains_bounded_and_heartbeat_does_not_refresh_semantic_activity() -> None:
    view = RunViewModel(milestone_capacity=8)
    correlation = RunCorrelation.fresh()
    view.begin_run(1, owner="owner")
    start = RuntimeEvent.from_fields(RuntimeEventKind.STEP_COMPLETED, correlation, {"step_id": "step-1"}, timestamp="2026-01-01T00:00:00+00:00")
    heartbeat = RuntimeEvent.from_fields(RuntimeEventKind.CONTEXT_REFRESH, correlation, {"heartbeat": True}, timestamp="2026-01-01T00:00:10+00:00")
    view.apply(start)
    assert view.snapshot().last_activity_at == "2026-01-01T00:00:00+00:00"
    view.apply(heartbeat)
    assert view.snapshot().last_activity_at == "2026-01-01T00:00:00+00:00"
    for index in range(2000):
        view.apply(RuntimeEvent.from_fields(RuntimeEventKind.STEP_COMPLETED, correlation, {"step_id": f"s{index}"}))
    snapshot = view.snapshot()
    assert len(snapshot.milestones) <= 8
    assert len(command_handlers._view_snapshot(SimpleNamespace(view_model=view)).milestones) <= 8


def test_worker_output_uses_thread_local_sink_without_rebinding_process_stdout() -> None:
    original_stdout = sys.stdout

    def interact(*_args, **kwargs):
        assert sys.stdout is original_stdout
        kwargs["stream_callback"]("chunk-from-worker")
        emit_worker_output("legacy-seam-output")
        return SimpleNamespace(answer="ok")

    envelope = SubmissionEnvelope(1, "hello", "natural_text", "AGENTIC", "AGENTIC_SUBMIT", "PENDING_EXACT_TEXT", "hello", "natural", "owner")
    context = SimpleNamespace(
        application=SimpleNamespace(interact=interact),
        orchestrator=SimpleNamespace(),
        controller=None,
        approval_broker=None,
        view_model=None,
    )
    result = execute_submission(context, envelope, Event(), lambda _callback: None)

    assert isinstance(result, InteractiveWorkerResult)
    assert "chunk-from-worker" in result.stdout
    assert "legacy-seam-output" in result.stdout
    assert sys.stdout is original_stdout


def test_worker_error_terminalizes_view_projection_and_clears_live_fields() -> None:
    view = RunViewModel()
    correlation = RunCorrelation.fresh()
    view.begin_run(3, owner="worker")
    view.apply(RuntimeEvent.from_fields(RuntimeEventKind.TOOL_START, correlation, {"tool": "reader", "invocation_id": "inv"}))
    view.set_attention(True)
    output: list[str] = []
    context = SimpleNamespace(
        view_model=view,
        shell=SimpleNamespace(print_background=lambda value: output.append(str(value))),
    )
    interactive_rendering.render_worker_message(
        context,
        SimpleNamespace(run_generation=3, error=RuntimeError("provider failed")),
    )

    snapshot = view.snapshot()
    assert snapshot.state == "TERMINAL"
    assert snapshot.current_tool is None
    assert snapshot.current_invocation_id is None
    assert snapshot.attention_pending is False
    assert snapshot.error_count == 1
    assert any("provider failed" in line for line in output)


def test_live_stream_survives_active_composer_cancel_without_terminal_duplication() -> None:
    controller = InteractiveExecutionController()
    started = Event()
    cancelled = Event()
    cancel_holder: dict[str, Event] = {}
    assistant_chunks: list[str] = []
    diagnostics: list[str] = []

    def bind(event: Event):
        cancel_holder["event"] = event
        return lambda: None

    def interact(*_args, **kwargs):
        callback = kwargs["stream_callback"]
        callback("part-1")
        callback("-part-2")
        started.set()
        while not cancel_holder["event"].is_set():
            time.sleep(0.005)
        cancelled.set()
        return SimpleNamespace(status="failed", answer="part-1-part-2", error="cancelled")

    application = SimpleNamespace(interact=interact, bind_interactive_cancellation=bind)
    context = SimpleNamespace(
        application=application,
        orchestrator=SimpleNamespace(),
        controller=controller,
        approval_broker=None,
        view_model=None,
    )
    envelope = SubmissionEnvelope(
        0,
        "stream this",
        "natural_text",
        "AGENTIC",
        "AGENTIC_SUBMIT",
        "PENDING_EXACT_TEXT",
        "stream this",
        "natural",
        "owner",
    )

    with create_pipe_input() as pipe:
        shell = InteractiveShell(
            registry=DEFAULT_COMMAND_REGISTRY,
            input=pipe,
            output=DummyOutput(),
        )
        context.shell = shell
        shell.print_stream = lambda value: assistant_chunks.append(str(value))
        shell.print_background = lambda value, **_: diagnostics.append(str(value))
        shell.set_background_pump(lambda: interactive_rendering.drain_worker_stream(context))
        prompt_result: list[str | None] = []
        prompt_thread = Thread(target=lambda: prompt_result.append(shell.prompt("> ")))
        prompt_thread.start()
        try:
            accepted = controller.submit(
                envelope,
                lambda submitted, cancel_event, register: execute_submission(
                    context,
                    submitted,
                    cancel_event,
                    register,
                ),
            )
            assert accepted.disposition == "ACCEPTED"
            assert started.wait(2)
            deadline = time.monotonic() + 2
            while assistant_chunks != ["part-1", "-part-2"] and time.monotonic() < deadline:
                time.sleep(0.01)
            assert prompt_thread.is_alive()
            assert assistant_chunks == ["part-1", "-part-2"]

            assert controller.request_cancel().disposition == "CANCEL_REQUESTED"
            assert cancelled.wait(2)
            message = controller.wait_for_settlement(timeout_seconds=2)
            assert message is not None and message.assistant_streamed
            interactive_rendering.render_worker_message(context, message)
            assert assistant_chunks == ["part-1", "-part-2"]
            assert not any("part-1-part-2" in value for value in diagnostics)
            pipe.send_text("\r")
            prompt_thread.join(timeout=3)
            assert not prompt_thread.is_alive()
        finally:
            if prompt_thread.is_alive():
                pipe.send_text("\r")
                prompt_thread.join(timeout=3)
            shell.close()


def test_worker_stream_is_bounded_and_rejects_stale_generation_chunks() -> None:
    channel = BoundedWorkerStream(max_items=2, max_chars=5)
    channel.begin_generation(1)
    assert channel.publish(1, "assistant", "one")
    channel.finish_generation(1)
    assert [chunk.text for chunk in channel.poll(1)] == ["one"]

    channel.begin_generation(2)
    assert channel.publish(2, "assistant", "12")
    assert not channel.publish(1, "assistant", "stale")
    assert channel.publish(2, "diagnostic", "debug") is False
    status = channel.finish_generation(2)
    chunks = channel.poll(2)

    assert [chunk.text for chunk in chunks] == ["12", "deb"]
    assert [chunk.kind for chunk in chunks] == ["assistant", "diagnostic"]
    assert status.truncated is True
    assert status.dropped >= 1
    diagnostic_output: list[str] = []
    interactive_rendering.render_worker_stream(
        SimpleNamespace(shell=SimpleNamespace(print_background=diagnostic_output.append)),
        chunks[1],
    )
    assert diagnostic_output == ["[worker] deb"]


def test_rejected_first_assistant_publish_keeps_canonical_answer_visible() -> None:
    channel = BoundedWorkerStream(max_items=1, max_chars=4)
    channel.begin_generation(7)

    def interact(*_args, **kwargs):
        emit_worker_output("diag")
        kwargs["stream_callback"]("answer-from-model")
        return SimpleNamespace(status="succeeded", answer="canonical-answer")

    context = SimpleNamespace(
        application=SimpleNamespace(interact=interact),
        orchestrator=SimpleNamespace(),
        controller=SimpleNamespace(stream_channel=channel),
        approval_broker=None,
        view_model=None,
    )
    envelope = SubmissionEnvelope(
        7,
        "show answer",
        "natural_text",
        "AGENTIC",
        "AGENTIC_SUBMIT",
        "PENDING_EXACT_TEXT",
        "show answer",
        "natural",
        "owner",
    )

    result = execute_submission(context, envelope, Event(), lambda _callback: None)
    status = channel.finish_generation(7)
    output: list[str] = []
    interactive_rendering.render_worker_message(
        SimpleNamespace(shell=SimpleNamespace(print_background=lambda value: output.append(str(value))), controller=None),
        SimpleNamespace(run_generation=7, result=result),
    )

    assert result.assistant_streamed is False
    assert status.assistant_seen is False
    assert output == ["canonical-answer"]


def test_accepted_assistant_prefix_reports_truncation_without_claiming_full_answer() -> None:
    channel = BoundedWorkerStream(max_items=2, max_chars=6)
    channel.begin_generation(8)
    assert channel.publish(8, "assistant", "abcde") is True
    assert channel.publish(8, "assistant", "rest") is False
    status = channel.finish_generation(8)
    stream: list[str] = []
    output: list[str] = []
    context = SimpleNamespace(
        controller=SimpleNamespace(poll_stream=lambda: channel.poll(8)),
        shell=SimpleNamespace(
            print_stream=lambda value: stream.append(str(value)),
            print_background=lambda value: output.append(str(value)),
        ),
    )

    interactive_rendering.render_worker_message(
        context,
        SimpleNamespace(
            run_generation=8,
            result=SimpleNamespace(status="succeeded", answer="canonical-answer"),
            assistant_streamed=status.assistant_seen,
            assistant_stream_truncated=status.truncated,
        ),
    )

    assert status.assistant_seen is True
    assert status.truncated is True
    assert stream == ["abcde", "r"]
    assert any("resposta parcial" in value for value in output)
    assert "canonical-answer" not in output

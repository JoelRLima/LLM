"""D01: real textual admission/controller/worker routes with offline execution ports."""

from __future__ import annotations

from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest

from llm_agent.agent.llm.contracts import UnavailableModelGateway
from llm_agent.application.conversation import bind_conversation
from llm_agent.application.task_execution import _retain_runtime
from llm_agent.interactive.action_parser import parse_action
from llm_agent.interactive.controller import InteractiveExecutionController
from llm_agent.interfaces.cli import interactive_admission


class OfflineGateway(UnavailableModelGateway):
    provider_name = "openai_compatible"
    model = "offline-routing-test"


@pytest.fixture
def routing_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    def forbidden_network(*_args, **_kwargs):
        pytest.fail("D01 must not access HTTP or a model endpoint")

    monkeypatch.setattr("requests.sessions.Session.request", forbidden_network)
    calls = []
    started, release = Event(), Event()

    def interact(text, **kwargs):
        calls.append(("interact", text, kwargs))
        if text == "hold":
            started.set()
            if not release.wait(3):
                raise TimeoutError("test hold was not released")
        kwargs["stream_callback"]("offline-response")
        return SimpleNamespace(answer="offline-response")

    def resume(**kwargs):
        calls.append(("resume", kwargs))
        kwargs["stream_callback"]("offline-resume")
        return SimpleNamespace(answer="offline-resume")

    def run_tool(name, arguments, **kwargs):
        calls.append(("tool", name, arguments, kwargs))
        return SimpleNamespace(to_legacy_dict=lambda: {"ok": True, "data": "offline-search"})

    orchestrator = SimpleNamespace(
        operational_mode_label="READ_ONLY", mode_allows=lambda _required: False,
        tool_invocation_gateway=SimpleNamespace(run=run_tool),
        allowed_capabilities=frozenset({"network"}),
    )
    owner = SimpleNamespace(
        session=SimpleNamespace(gateway=OfflineGateway()),
        orchestrator=orchestrator, interact=interact, resume=resume,
    )
    runtime = _retain_runtime(owner)
    controller = InteractiveExecutionController()
    ctx = SimpleNamespace(
        task_execution=runtime, conversation=bind_conversation(runtime),
        config={"model": "offline-routing-test"}, workspace=SimpleNamespace(root=tmp_path),
        controller=controller, approval_broker=None, view_model=None,
        event_mailbox=None, shell=None,
        calls=calls, started=started, release=release,
    )
    try:
        yield ctx
    finally:
        release.set()
        controller.shutdown(timeout_seconds=3)


def _settle(ctx, text):
    assert interactive_admission.handle_input(text, ctx) is False
    message = ctx.controller.wait_for_settlement(timeout_seconds=3)
    assert message is not None
    assert message.error is None, message.error
    assert not ctx.controller.is_busy()
    return message.result.result


def test_code_analyze_reaches_real_local_workflow(routing_context):
    ctx = routing_context
    target = ctx.workspace.root / "sample.py"
    target.write_text("value = 1\n", encoding="utf-8")
    assert parse_action("/code analyze sample.py").action_id == "interaction.code_submit"
    result = _settle(ctx, "/code analyze sample.py")
    assert result.kind == "executed" and result.status == "succeeded"
    assert any(kind == "code_analysis" for kind, _content in result.artifacts)
    assert target.read_text(encoding="utf-8") == "value = 1\n"
    assert ctx.calls == []


def test_code_write_stays_denied_by_existing_mode(routing_context):
    ctx = routing_context
    result = _settle(ctx, "/code modify sample.py -- change value")
    assert result.kind == "mode_denied" and result.status == "blocked"
    assert not (ctx.workspace.root / "sample.py").exists()
    assert ctx.calls == []


def test_search_preserves_tool_gateway_authority_boundary(routing_context):
    ctx = routing_context
    result = _settle(ctx, "/search offline subject")
    assert result == {"ok": True, "data": "offline-search"}
    assert len(ctx.calls) == 1
    kind, name, arguments, options = ctx.calls[0]
    assert (kind, name, arguments) == ("tool", "web_search", {"query": "offline subject"})
    assert options["active_skills"] is None
    assert options["allowed_capabilities"] == frozenset({"network"})
    assert isinstance(options["cancellation_token"], Event)


@pytest.mark.parametrize("text", ["/agent explicit subject", "/agente explicit subject"])
def test_agent_keeps_explicit_task_boundary(routing_context, text):
    ctx = routing_context
    _settle(ctx, text)
    assert len(ctx.calls) == 1
    kind, payload, options = ctx.calls[0]
    assert (kind, payload) == ("interact", "explicit subject")
    assert options["boundary"] == "task"
    assert options["visible_user_text"] == text
    assert options["task_payload"] == "explicit subject"
    assert callable(options["stream_callback"])
    assert "offline-response" in "".join(c.text for c in ctx.controller.poll_stream())


@pytest.mark.parametrize("text", ["/retry", "/retomar"])
def test_retry_uses_explicit_resume(routing_context, text):
    ctx = routing_context
    _settle(ctx, text)
    assert len(ctx.calls) == 1
    assert ctx.calls[0][0] == "resume"
    assert callable(ctx.calls[0][1]["stream_callback"])
    assert "offline-resume" in "".join(c.text for c in ctx.controller.poll_stream())


def test_natural_text_keeps_natural_boundary(routing_context):
    ctx = routing_context
    _settle(ctx, "ordinary subject")
    kind, payload, options = ctx.calls[0]
    assert (kind, payload, options["boundary"]) == ("interact", "ordinary subject", "natural")


@pytest.mark.parametrize("text", ["/code help", "/code ajuda"])
def test_code_help_stays_local_without_worker(routing_context, capsys, text):
    ctx = routing_context
    assert interactive_admission.handle_input(text, ctx) is False
    assert ctx.controller.current_generation is None
    assert ctx.controller.wait_for_settlement() is None
    assert ctx.calls == []
    assert "/code analyze" in capsys.readouterr().out


def test_pending_keeps_canonical_envelope_and_consume_once(routing_context):
    ctx = routing_context
    (ctx.workspace.root / "sample.py").write_text("value = 1\n", encoding="utf-8")
    assert interactive_admission.handle_input("hold", ctx) is False
    assert ctx.started.wait(2)
    assert interactive_admission.handle_input("/code analyze sample.py", ctx) is False
    item, = ctx.controller.pending.list()
    assert item.envelope.command_id == "interaction.code_submit"
    assert item.envelope.payload == "analyze sample.py"
    assert item.envelope.visible_text == "/code analyze sample.py"
    ctx.release.set()
    assert ctx.controller.wait_for_settlement(timeout_seconds=3).error is None
    result = _settle(ctx, f"/pending send {item.pending_id}")
    assert result.kind == "executed" and result.status == "succeeded"
    assert ctx.controller.pending.list() == ()
    assert len(ctx.calls) == 1 and ctx.calls[0][1] == "hold"
    assert interactive_admission.handle_input(f"/pending send {item.pending_id}", ctx) is False
    assert ctx.controller.wait_for_settlement() is None


def test_search_failure_settles_and_next_natural_request_works(routing_context):
    ctx = routing_context

    def failed_search(*_args, **_kwargs):
        raise RuntimeError("offline search failure")

    ctx.task_execution._owner.orchestrator.tool_invocation_gateway.run = failed_search
    assert interactive_admission.handle_input("/search unavailable", ctx) is False
    message = ctx.controller.wait_for_settlement(timeout_seconds=3)
    assert isinstance(message.error, RuntimeError)
    assert str(message.error) == "offline search failure"
    assert not ctx.controller.is_busy() and ctx.calls == []
    _settle(ctx, "after failure")
    assert ctx.calls[0][2]["boundary"] == "natural"


def test_unknown_command_is_rejected_without_submission(routing_context):
    ctx = routing_context
    assert interactive_admission.handle_input("/unknown-d01-command", ctx) is False
    assert ctx.calls == [] and not ctx.controller.is_busy()
    assert ctx.controller.wait_for_settlement() is None

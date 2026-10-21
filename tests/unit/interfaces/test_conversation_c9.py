"""Legacy CLI parity across the C9 Application boundary."""

import io
from types import SimpleNamespace

import pytest
from rich.console import Console

from llm_agent.agent.llm.errors import ModelConnectionError as AgentModelConnectionError
from llm_agent.agent.llm.errors import ModelTimeoutError as AgentModelTimeoutError
from llm_agent.agent.llm.session import ChatSession
from llm_agent.application.conversation import bind_conversation
from llm_agent.application.task_execution import _retain_runtime
from llm_agent.interfaces.cli import bootstrap, chat


def _session(response="answer", failure=None):
    session = object.__new__(ChatSession)
    session.messages = [{"role": "system", "content": "prompt"}]
    session.thinking_budget = 0
    session.model_profile = SimpleNamespace(model="m", provider="p")
    session.get_effective_system_prompt = lambda: "prompt"
    counts = {"builds": 0, "executions": 0, "events": []}

    def build(**kwargs):
        counts["builds"] += 1
        return SimpleNamespace(model="m", temperature=0.6, max_output_tokens=17,
                               stream=True, structured_output=None, messages=session.messages)

    def stream(request, callbacks):
        counts["executions"] += 1
        assert list(callbacks) == ["on_raw_line", "on_thinking_chunk", "on_content_chunk", "on_error", "on_done"]
        if failure is not None:
            raise failure
        callbacks["on_raw_line"]("raw")
        callbacks["on_thinking_chunk"]("thought")
        if response:
            callbacks["on_content_chunk"](response)
        callbacks["on_done"]({})
        return response

    session.build_request = build
    session.consume_stream_request = stream
    return session, counts


@pytest.mark.parametrize("diagnostic", [0, 2])
@pytest.mark.parametrize("response", ["answer", ""])
def test_success_empty_preview_and_single_execution(diagnostic, response):
    session, counts = _session(response)
    output = io.StringIO()
    console = Console(file=output, color_system=None, width=120)
    chat.run_chat_turn(console, bind_conversation(_retain_runtime(SimpleNamespace(session=session))), "user", diagnostic)
    assert counts["builds"] == (2 if diagnostic == 2 else 1)
    assert counts["executions"] == 1
    assert session.messages[1:] == [{"role": "user", "content": "user"}] + (
        [{"role": "assistant", "content": response}] if response else []
    )
    if diagnostic == 2:
        assert '"num_messages": 2' in output.getvalue()
    assert ("answer" if response else "sua mensagem foi mantida") in output.getvalue()


@pytest.mark.parametrize("failure, text, retained", [
    (AgentModelTimeoutError("timeout"), "Tempo limite da requisição excedido.", False),
    (AgentModelConnectionError("offline"), "Erro de conexão: offline", False),
    (RuntimeError("generic"), "Erro inesperado: generic", False),
    (KeyboardInterrupt(), "Interrompido pelo usuário.", True),
])
def test_error_rendering_rollback_interrupt_and_no_retry(failure, text, retained):
    session, counts = _session(failure=failure)
    output = io.StringIO()
    chat.run_chat_turn(Console(file=output, color_system=None, width=120),
                       bind_conversation(_retain_runtime(SimpleNamespace(session=session))), "user", 0)
    assert text in output.getvalue()
    assert session.messages[1:] == ([{"role": "user", "content": "user"}] if retained else [])
    assert counts["executions"] == counts["builds"] == 1


def test_preview_failure_remains_outside_request_catch():
    session, counts = _session()
    failure = RuntimeError("preview")

    def build(**kwargs):
        raise failure

    session.build_request = build
    with pytest.raises(RuntimeError) as caught:
        chat.run_chat_turn(Console(file=io.StringIO()),
                           bind_conversation(_retain_runtime(SimpleNamespace(session=session))), "user", 2)
    assert caught.value is failure
    assert session.messages[-1] == {"role": "user", "content": "user"}
    assert counts["executions"] == 0


def test_composition_binds_once_and_keeps_explicit_empty_config():
    session, _ = _session()
    app = SimpleNamespace(session=session, orchestrator=object(), config={}, paths=None,
                          workspace=None, workspace_paths=None)
    ctx = bootstrap.context_from_application(_retain_runtime(app))
    runtime = ctx.conversation
    assert runtime._session is session and ctx.config is app.config
    assert not hasattr(ctx, "session")
    assert ctx.conversation is runtime

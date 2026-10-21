"""C9 session identity, transcript and request-count sentinels."""

from types import SimpleNamespace

import pytest

from llm_agent.agent.llm.session import ChatSession
from llm_agent.agent.runtime.worker_output import bind_worker_output
from llm_agent.application import conversation as api
from llm_agent.application.task_execution import _retain_runtime


class SentinelSession(ChatSession):
    def __init__(self):
        self.messages = [{"role": "system", "content": "original"}]
        self.thinking_budget = 0
        self.gateway = object()
        self.model_profile = SimpleNamespace(model="model", provider="provider")
        self.builds = 0
        self.executions = 0
        self.response = "answer"
        self.failure = None
        self.callbacks = None

    def get_effective_system_prompt(self):
        return self.messages[0]["content"]

    def build_request(self, *, stream=True):
        self.builds += 1
        return SimpleNamespace(model="model", temperature=0.6, max_output_tokens=17,
                               stream=stream, structured_output=None, messages=self.messages)

    def consume_stream_request(self, request, callbacks):
        self.executions += 1
        self.callbacks = callbacks
        if self.failure is not None:
            raise self.failure
        return self.response


def test_same_session_state_and_private_gateway_identity():
    session = SentinelSession()
    runtime = api.bind_conversation(_retain_runtime(SimpleNamespace(session=session)))
    assert runtime._session is session
    api.configure_conversation(runtime, system_prompt="changed", thinking_budget=-5)
    view = api.read_conversation(runtime)
    assert (view.effective_system_prompt, view.thinking_budget, view.model, view.provider) == (
        "changed", -5, "model", "provider",
    )
    api.configure_conversation(runtime)
    assert session.thinking_budget == -5
    assert api._resolve_gateway(runtime) is session.gateway
    api.append_legacy_transcript(runtime, "old answer")
    api.append_legacy_transcript(runtime, "new answer", user_text="new user")
    assert [m["role"] for m in session.messages] == ["system", "assistant", "user", "assistant"]
    api.execute_history_command(runtime, "clear")
    assert session.messages == [{"role": "system", "content": "changed"}]


@pytest.mark.parametrize("preview", [False, True])
def test_turn_identity_counts_and_callbacks(preview):
    session = SentinelSession()
    runtime = api.bind_conversation(_retain_runtime(SimpleNamespace(session=session)))
    turn = api.begin_chat_turn(runtime, "user", include_preview=preview)
    assert turn._conversation is runtime
    assert session.builds == int(preview)
    if preview:
        assert turn.preview == api.ChatRequestPreview("model", 0.6, 17, True, None, 2)
    callbacks = {"on_content_chunk": lambda _: None}
    response = api.stream_chat_turn(turn, callbacks)
    api.finish_chat_turn(turn, response)
    assert response == "answer" and session.callbacks is callbacks
    assert session.builds == 1 + int(preview) and session.executions == 1
    assert session.messages[1:] == [
        {"role": "user", "content": "user"}, {"role": "assistant", "content": "answer"},
    ]


def test_chat_turn_publication_uses_current_sink_then_its_retained_fallback() -> None:
    session = SentinelSession()
    runtime = api.bind_conversation(_retain_runtime(SimpleNamespace(session=session)))
    fallback_calls: list[tuple[object, str]] = []
    original_value = object()
    turn = api.begin_chat_turn(
        runtime,
        "user",
        presentation_fallback=lambda item, end: fallback_calls.append((item, end)),
    )
    sink_a: list[str] = []
    sink_b: list[str] = []

    with bind_worker_output(sink_a.append):
        turn.present_stream_text(original_value, end="!")
        with bind_worker_output(sink_b.append):
            turn.present_stream_text("nested", end="")
        turn.present_stream_text("restored", end=".")
    turn.present_stream_text(original_value, end="?")

    assert sink_a == [f"{original_value}!", "restored."]
    assert sink_b == ["nested"]
    assert fallback_calls == [(original_value, "?")]


@pytest.mark.parametrize("kind", ["empty", "interrupt", "failure"])
def test_current_transcript_asymmetry(kind):
    session = SentinelSession()
    runtime = api.bind_conversation(_retain_runtime(SimpleNamespace(session=session)))
    turn = api.begin_chat_turn(runtime, "user")
    if kind == "interrupt":
        session.failure = KeyboardInterrupt()
        with pytest.raises(KeyboardInterrupt):
            api.stream_chat_turn(turn, {})
        api.finish_chat_turn(turn, interrupted=True)
    elif kind == "failure":
        session.failure = RuntimeError("failure")
        with pytest.raises(RuntimeError):
            api.stream_chat_turn(turn, {})
        api.finish_chat_turn(turn, failed=True)
    else:
        session.response = ""
        api.finish_chat_turn(turn, api.stream_chat_turn(turn, {}))
    assert session.executions == 1
    assert session.messages[1:] == ([] if kind == "failure" else [{"role": "user", "content": "user"}])


def test_history_exact_projection_and_invalid_load_does_not_install(tmp_path):
    session = SentinelSession()
    runtime = api.bind_conversation(_retain_runtime(SimpleNamespace(session=session)))
    path = str(tmp_path / "history.json")
    session.add_user_message("saved")
    expected = session.messages.copy()
    assert api.execute_history_command(runtime, "save", path) == api.HistoryOutcome(True, "")
    api.execute_history_command(runtime, "clear")
    assert api.execute_history_command(runtime, "load", path) == api.HistoryOutcome(True, "")
    assert session.messages == expected
    (tmp_path / "history.json").write_text('[{"role":"bogus","content":"bad"}]')
    previous = session.messages
    owner_result = session.load_from_file(path)
    assert api.execute_history_command(runtime, "load", path) == api.HistoryOutcome(*owner_result)
    assert owner_result[0] is False and session.messages is previous
    session.save_to_file = lambda p: (True, "success payload")
    assert api.execute_history_command(runtime, "save", path) == api.HistoryOutcome(True, "success payload")


def test_public_identity_opacity_and_immutable_dtos():
    for name in api.__all__[:5]:
        cls = getattr(api, name)
        assert cls.__module__ == "llm_agent.application.conversation"
        assert not issubclass(cls, ChatSession)
    runtime = api.bind_conversation(_retain_runtime(SimpleNamespace(session=SentinelSession())))
    for name in ("session", "gateway", "messages", "model_profile", "config", "thinking_budget", "close"):
        assert not hasattr(runtime, name)
    assert not hasattr(type(runtime), "__getattr__")
    with pytest.raises(AttributeError):
        api.read_conversation(runtime).model = "other"

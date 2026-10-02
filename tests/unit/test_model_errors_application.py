"""Application exception identity and exact transport fidelity."""

from types import SimpleNamespace

import pytest

from llm_agent.agent.llm.errors import ModelConnectionError as AgentModelConnectionError
from llm_agent.agent.llm.errors import ModelTimeoutError as AgentModelTimeoutError
from llm_agent.application import conversation, model_errors
from llm_agent.application.task_execution import _retain_runtime


@pytest.mark.parametrize("owner, application", [
    (AgentModelConnectionError, model_errors.ModelConnectionError),
    (AgentModelTimeoutError, model_errors.ModelTimeoutError),
])
@pytest.mark.parametrize("preview", [False, True])
def test_translation_preserves_args_text_cause_and_distinct_identity(owner, application, preview):
    exc = owner("payload")
    exc.args = ("payload", 42)

    def fail(**kwargs):
        raise exc

    session = SimpleNamespace(add_user_message=lambda _: None, build_request=fail)
    runtime = conversation.bind_conversation(_retain_runtime(SimpleNamespace(session=session)))
    with pytest.raises(application) as caught:
        turn = conversation.begin_chat_turn(runtime, "user", include_preview=preview)
        conversation.stream_chat_turn(turn, {})
    translated = caught.value
    assert type(translated) is application
    assert translated.args == exc.args and str(translated) == str(exc)
    assert translated.__cause__ is exc
    assert application.__module__ == "llm_agent.application.model_errors"
    assert issubclass(application, RuntimeError)
    assert issubclass(application, ConnectionError if owner is AgentModelConnectionError else TimeoutError)
    assert not issubclass(application, owner)
    assert not hasattr(translated, "response")


@pytest.mark.parametrize("exc", [RuntimeError("generic"), KeyboardInterrupt(), SystemExit(), GeneratorExit()])
def test_unrelated_exception_identity_is_preserved(exc):
    def fail(**kwargs):
        raise exc

    runtime = conversation.bind_conversation(_retain_runtime(SimpleNamespace(session=SimpleNamespace(
        add_user_message=lambda _: None, build_request=fail,
    ))))
    turn = conversation.begin_chat_turn(runtime, "user")
    with pytest.raises(type(exc)) as caught:
        conversation.stream_chat_turn(turn, {})
    assert caught.value is exc

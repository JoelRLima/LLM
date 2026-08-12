import logging
from types import SimpleNamespace

import pytest

from agent.final_response import FinalResponder
from agent.llm.model_client import ModelProviderError


class FailingSession:
    def __init__(self, secret: str):
        self.secret = secret
        self.messages = [{"role": "system", "content": "system"}]

    def add_user_message(self, content: str) -> None:
        self.messages.append({"role": "user", "content": content})

    def build_payload(self):
        return {"messages": self.messages}

    def send_non_streaming_request(self, payload):
        del payload
        raise RuntimeError(self.secret)

    def send_request(self, payload, stream=True):
        del payload, stream
        raise RuntimeError(self.secret)

    def remove_last_user_message(self) -> None:
        if self.messages and self.messages[-1]["role"] == "user":
            self.messages.pop()


@pytest.mark.parametrize("streaming", [False, True])
def test_final_provider_failure_does_not_log_secret(caplog, streaming):
    secret = "api_key=TOPSECRET Authorization: Bearer TOPSECRET token=TOPSECRET password=TOPSECRET"
    session = FailingSession(secret)
    responder = FinalResponder(SimpleNamespace(session=session, agent_state=SimpleNamespace(tool_history=[])))
    caplog.set_level(logging.ERROR)

    with pytest.raises(ModelProviderError, match="Model provider request failed"):
        responder._request_answer(lambda _chunk: None) if streaming else responder._request_answer(None)

    for marker in ("TOPSECRET", "Authorization: Bearer", "api_key=", "token=", "password="):
        assert marker not in caplog.text

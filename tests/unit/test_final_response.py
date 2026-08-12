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


class GroundingSession:
    def __init__(self) -> None:
        self.messages = [{"role": "system", "content": "system"}]
        self.final_prompt = ""

    def add_user_message(self, content: str) -> None:
        self.messages.append({"role": "user", "content": content})

    def build_payload(self):
        return {"messages": list(self.messages)}

    def send_non_streaming_request(self, payload):
        self.final_prompt = payload["messages"][-1]["content"]
        assert "observação: []" in self.final_prompt
        assert "não invente arquivos" in self.final_prompt
        return "O workspace está vazio; nenhum item foi observado."

    def remove_last_user_message(self) -> None:
        if self.messages and self.messages[-1]["role"] == "user":
            self.messages.pop()


def test_empty_tool_observation_reaches_grounded_synthesis() -> None:
    session = GroundingSession()
    state = SimpleNamespace(
        tool_history=[
            {
                "tool": "directory_lister",
                "args": {"path": "."},
                "result": {
                    "ok": True,
                    "status": "succeeded",
                    "data": [],
                    "message": "0 itens encontrados em '.'.",
                },
            }
        ],
        conversation_history=[],
    )
    responder = FinalResponder(SimpleNamespace(session=session, agent_state=state))

    answer = responder.build_final_answer("Liste os arquivos e diretórios.")

    assert answer == "O workspace está vazio; nenhum item foi observado."
    assert "observação: []" in session.final_prompt


def test_tool_summary_preserves_positive_and_failed_observations() -> None:
    state = SimpleNamespace(
        tool_history=[
            {
                "tool": "directory_lister",
                "result": {
                    "ok": True,
                    "status": "succeeded",
                    "data": [{"name": "sentinel_unique_8472.txt", "type": "file"}],
                },
            },
            {
                "tool": "file_reader",
                "result": {
                    "ok": False,
                    "status": "failed",
                    "data": "",
                    "error": "acesso negado",
                },
            },
        ]
    )
    responder = FinalResponder(SimpleNamespace(agent_state=state))

    summary = responder._tool_results_summary()

    assert "sentinel_unique_8472.txt" in summary
    assert "status: failed" in summary
    assert "error: acesso negado" in summary
    assert 'observação: ""' in summary


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

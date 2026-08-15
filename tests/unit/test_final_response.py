import logging
from types import SimpleNamespace

import pytest

from agent.final_response import MAX_TOOL_RESULTS_SUMMARY_CHARS, FinalResponder
from agent.llm.model_client import ModelProviderError
from agent.reporting.operational_outcome import OperationalOutcome


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


class ForbiddenOperationalSession:
    def __getattr__(self, name):
        raise AssertionError(f"model session must not be used for operational truth: {name}")


def _outcome(**overrides):
    values = {
        "terminal_status": "complete",
        "requested_effects": ("write",),
        "executed_effects": (),
        "waived_effects": ("write",),
        "pending_effects": (),
        "mutation_occurred": False,
        "validation_status": None,
        "rollback_occurred": False,
        "blocked_reason": None,
        "failure_reason": None,
        "files_affected": (),
        "evidence_invocation_ids": ("read-1",),
    }
    values.update(overrides)
    return OperationalOutcome(**values)


def test_operational_outcome_cannot_be_overridden_by_model_prose() -> None:
    state = SimpleNamespace(conversation_history=[])
    responder = FinalResponder(
        SimpleNamespace(session=ForbiddenOperationalSession(), agent_state=state)
    )

    answer = responder.build_final_answer(
        "altere o arquivo",
        operational_outcome=_outcome(),
    )

    assert answer.startswith("Nenhuma escrita foi executada.")
    assert "alterad" not in answer.casefold()
    assert state.conversation_history[-1]["agent"] == answer


def test_operational_outcome_renders_real_write_and_unavailable_validation() -> None:
    state = SimpleNamespace(conversation_history=[])
    responder = FinalResponder(
        SimpleNamespace(session=ForbiddenOperationalSession(), agent_state=state)
    )

    answer = responder.build_final_answer(
        "altere o arquivo",
        operational_outcome=_outcome(
            executed_effects=("write",),
            waived_effects=(),
            mutation_occurred=True,
            validation_status="unavailable",
            files_affected=("controle.txt",),
        ),
    )

    assert "alteração foi aplicada" in answer
    assert "controle.txt" in answer
    assert "não havia validação aplicável" in answer


def test_operational_outcome_renders_rollback_as_no_persisted_write() -> None:
    state = SimpleNamespace(conversation_history=[])
    responder = FinalResponder(
        SimpleNamespace(session=ForbiddenOperationalSession(), agent_state=state)
    )

    answer = responder.build_final_answer(
        "altere o arquivo",
        operational_outcome=_outcome(
            executed_effects=("write",),
            waived_effects=(),
            mutation_occurred=True,
            rollback_occurred=True,
            validation_status="failed",
            files_affected=("controle.txt",),
        ),
    )

    assert "foi revertida" in answer
    assert "nenhuma escrita persistiu" in answer
    assert "foi aplicada" not in answer


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
                    "executed": True,
                    "error_code": "TOOL_ERROR",
                    "error": "acesso negado",
                },
            },
        ]
    )
    responder = FinalResponder(SimpleNamespace(agent_state=state))

    summary = responder._tool_results_summary()

    assert "sentinel_unique_8472.txt" in summary
    assert '"status":"failed"' in summary
    assert '"executed":true' in summary
    assert '"error_code":"TOOL_ERROR"' in summary
    assert "acesso negado" not in summary
    assert 'observação: ""' in summary


@pytest.mark.parametrize(
    ("data", "serialized"),
    (([], "[]"), ({}, "{}"), ("", '""'), (None, "null")),
)
def test_tool_summary_preserves_each_falsy_data_semantic(data, serialized) -> None:
    state = SimpleNamespace(
        tool_history=[
            {
                "tool": "probe",
                "result": {"ok": True, "status": "succeeded", "data": data},
            }
        ]
    )

    summary = FinalResponder(SimpleNamespace(agent_state=state))._tool_results_summary()

    assert f"observação: {serialized}" in summary


def test_tool_summary_distinguishes_missing_data_from_none() -> None:
    state = SimpleNamespace(
        tool_history=[
            {"tool": "missing", "result": {"ok": True, "status": "succeeded"}},
            {
                "tool": "none",
                "result": {"ok": True, "status": "succeeded", "data": None},
            },
        ]
    )

    summary = FinalResponder(SimpleNamespace(agent_state=state))._tool_results_summary()

    assert "observação: <data ausente>" in summary
    assert "observação: null" in summary


def test_tool_summary_preserves_multiple_results_in_order() -> None:
    state = SimpleNamespace(
        tool_history=[
            {
                "tool": "first",
                "result": {"ok": True, "status": "succeeded", "data": {"first": 1}},
            },
            {
                "tool": "empty",
                "result": {"ok": True, "status": "succeeded", "data": []},
            },
            {
                "tool": "failed",
                "result": {
                    "ok": False,
                    "status": "failed",
                    "data": None,
                    "error_code": "TOOL_ERROR",
                    "error": "conteúdo interno",
                },
            },
        ]
    )

    summary = FinalResponder(SimpleNamespace(agent_state=state))._tool_results_summary()

    assert summary.index('"tool":"first"') < summary.index('"tool":"empty"')
    assert summary.index('"tool":"empty"') < summary.index('"tool":"failed"')
    assert "conteúdo interno" not in summary
    assert summary.count("observação:") == 3


def test_tool_summary_does_not_forward_raw_error_or_message_secrets() -> None:
    secret = "api_key=TOPSECRET Authorization: Bearer TOPSECRET token=TOPSECRET password=TOPSECRET"
    state = SimpleNamespace(
        tool_history=[
            {
                "tool": "failed",
                "result": {
                    "ok": False,
                    "status": "failed",
                    "data": None,
                    "error_code": "TOOL_ERROR",
                    "message": secret,
                    "error": secret,
                },
            }
        ]
    )

    summary = FinalResponder(SimpleNamespace(agent_state=state))._tool_results_summary()

    assert '"error_code":"TOOL_ERROR"' in summary
    for marker in ("TOPSECRET", "Authorization: Bearer", "api_key=", "token=", "password="):
        assert marker not in summary


def test_tool_summary_has_one_global_budget_and_keeps_every_result() -> None:
    history = [
        {
            "tool": f"tool_{index}",
            "result": {"ok": True, "status": "succeeded", "data": "x" * 10_000},
        }
        for index in range(60)
    ]
    state = SimpleNamespace(tool_history=history)

    summary = FinalResponder(SimpleNamespace(agent_state=state))._tool_results_summary()

    assert len(summary) <= MAX_TOOL_RESULTS_SUMMARY_CHARS
    assert summary.count("--- Resultado de ferramenta ---") == len(history)
    assert '"tool":"tool_0"' in summary
    assert '"tool":"tool_59"' in summary


def test_tool_summary_budget_includes_json_escaped_metadata() -> None:
    history = [
        {
            "tool": "\x00" * 32,
            "result": {
                "ok": True,
                "status": "succeeded",
                "executed": True,
                "error_code": "APPLICATION_AUTHORITY_MISSING",
                "data": "x" * 10_000,
            },
        }
        for _ in range(60)
    ]
    state = SimpleNamespace(tool_history=history)

    summary = FinalResponder(SimpleNamespace(agent_state=state))._tool_results_summary()

    assert len(summary) <= MAX_TOOL_RESULTS_SUMMARY_CHARS
    assert "\x00" not in summary
    assert summary.count("--- Resultado de ferramenta ---") == len(history)


def test_tool_summary_drops_untrusted_error_code() -> None:
    state = SimpleNamespace(
        tool_history=[
            {
                "tool": "failed",
                "result": {
                    "ok": False,
                    "status": "failed",
                    "data": None,
                    "error_code": "TOPSECRET",
                },
            }
        ]
    )

    summary = FinalResponder(SimpleNamespace(agent_state=state))._tool_results_summary()

    assert "TOPSECRET" not in summary
    assert '"error_code":null' in summary


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

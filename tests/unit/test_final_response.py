import logging
from types import SimpleNamespace

import pytest

from agent.final_response import MAX_TOOL_RESULTS_SUMMARY_CHARS, FinalResponder
from agent.llm.model_client import ModelProviderError
from agent.llm.session import ChatSession
from agent.reporting.operational_outcome import OperationalOutcome
from agent.runtime.budget import BudgetExhausted
from agent.tools.contracts import ToolDescriptor


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
        assert '"value":[]' in self.final_prompt
        assert '"present":true' in self.final_prompt
        assert "não invente arquivos" in self.final_prompt
        return "O workspace está vazio; nenhum item foi observado."

    def remove_last_user_message(self) -> None:
        if self.messages and self.messages[-1]["role"] == "user":
            self.messages.pop()


class ForbiddenOperationalSession:
    def __getattr__(self, name):
        raise AssertionError(f"model session must not be used for operational truth: {name}")


class _FinalGateway:
    provider_name = "final-provider"
    model = "final-model"

    def __init__(self) -> None:
        self.calls = 0

    def build_payload(self, request):
        return {"messages": list(request.messages)}

    def complete_payload(self, payload):
        del payload
        self.calls += 1
        return "resposta"


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
    assert '"value":[]' in session.final_prompt


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
    assert '"present":true' in summary
    assert '"chars":0' in summary
    assert "acesso negado" not in summary
    assert '"value":""' in summary


def test_analysis_notes_cannot_bypass_canonical_observations() -> None:
    state = SimpleNamespace(
        tool_history=[
            {
                "tool": "file_reader",
                "result": {"ok": True, "status": "succeeded", "data": "observed"},
            }
        ]
    )
    responder = FinalResponder(
        SimpleNamespace(agent_state=state, tool_registry=None)
    )

    prompt = responder._build_prompt("objetivo", "nota derivada")

    assert "authoritative_tool_observation" in prompt
    assert "not proof" in prompt.casefold() or "nao prova" in prompt.casefold()
    assert "nao instrucoes" in prompt.casefold()


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

    assert f'"value":{serialized}' in summary


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

    assert '"present":false' in summary
    assert '"type":"missing"' in summary
    assert '"present":true' in summary
    assert '"value":null' in summary


def test_two_successful_empty_reads_are_explicit_model_observations() -> None:
    state = SimpleNamespace(
        tool_history=[
            {
                "tool": "file_reader",
                "args": {"file_path": "a.txt"},
                "result": {
                    "ok": True,
                    "status": "succeeded",
                    "data": "",
                    "artifacts": [{"metadata": {"complete": True}}],
                },
            },
            {
                "tool": "file_reader",
                "args": {"file_path": "b.txt"},
                "result": {
                    "ok": True,
                    "status": "succeeded",
                    "data": "",
                    "artifacts": [{"metadata": {"complete": True}}],
                },
            },
        ]
    )

    responder = FinalResponder(SimpleNamespace(agent_state=state))
    summary = responder._tool_results_summary()
    prompt = responder._build_prompt("compare a.txt and b.txt", "")

    assert summary.count('"status":"succeeded"') == 2
    assert summary.count('"present":true') == 2
    assert summary.count('"chars":0') == 2
    assert summary.count('"complete":true') == 2
    assert 'present=true' in prompt
    assert '"chars":0' in prompt


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
    assert summary.count('"observation":') == 3


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
    assert summary.count('"observation":') == len(history)
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
    assert summary.count('"observation":') == len(history)


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
    assert '"error_code"' not in summary


def test_final_grounding_exposes_actual_query_not_objective_text() -> None:
    descriptor = ToolDescriptor(
        "grep", "grep", public_invocation_fields={"path", "pattern"}
    )
    state = SimpleNamespace(
        tool_history=[
            {
                "tool": "grep",
                "args": {"path": ".", "pattern": "controle.txt"},
                "result": {
                    "ok": True,
                    "status": "succeeded",
                    "executed": True,
                    "data": [],
                },
            }
        ]
    )
    registry = SimpleNamespace(descriptor=lambda _name: descriptor)

    summary = FinalResponder(
        SimpleNamespace(agent_state=state, tool_registry=registry)
    )._tool_results_summary()

    assert '"pattern":"controle.txt"' in summary
    assert "modificado" not in summary


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


def test_final_responder_refuses_n_plus_one_provider_call() -> None:
    gateway = _FinalGateway()
    session = ChatSession(
        "system",
        {"model": "final-model", "max_model_calls": 1},
        gateway=gateway,
    )
    session.send_non_streaming_request({})
    responder = FinalResponder(
        SimpleNamespace(session=session, agent_state=SimpleNamespace(tool_history=[]))
    )

    with pytest.raises(BudgetExhausted):
        responder._request_answer(None)

    assert gateway.calls == 1

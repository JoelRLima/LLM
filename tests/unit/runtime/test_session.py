import pytest

from agent.llm.session import ChatSession


@pytest.fixture
def session():
    config = {
        "api_url": "http://127.0.0.1:8080",
        "model": "test",
        "temperature": 0.5,
        "max_tokens": 1024,
        "timeout": 30
    }
    return ChatSession("System inicial", config)

def test_inicializacao(session):
    assert len(session.messages) == 1
    assert session.messages[0]["content"] == "System inicial"
    assert session.thinking_budget == 0

def test_set_system_prompt(session):
    session.set_system_prompt("Novo system")
    assert session.messages[0]["content"] == "Novo system"
    assert session.get_effective_system_prompt() == "Novo system"

def test_effective_prompt_com_thinking(session):
    session.thinking_budget = 1000
    effective = session.get_effective_system_prompt()
    assert "[THINKING]" in effective
    assert "1000" in effective

def test_add_messages(session):
    session.add_user_message("Olá")
    session.add_assistant_message("Oi")
    assert len(session.messages) == 3
    assert session.messages[1] == {"role": "user", "content": "Olá"}
    assert session.messages[2] == {"role": "assistant", "content": "Oi"}

def test_remove_last_user_message(session):
    session.add_user_message("Ola")
    session.remove_last_user_message()
    assert len(session.messages) == 1

def test_clear_history(session):
    session.add_user_message("Mensagem perdida")
    session.clear_history()
    assert len(session.messages) == 1
    assert session.messages[0]["role"] == "system"

def test_build_request(session):
    session.add_user_message("Ping")
    request = session.build_request()
    assert request.model == "test"
    assert request.temperature == 0.5
    assert len(request.messages) == 2
    assert request.messages[1].content == "Ping"
    assert request.stream is True
    assert request.reasoning_budget == 0

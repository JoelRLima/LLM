import json

from agent.llm import router
from agent.llm.contracts import ModelResponse
from agent.llm.session import ChatSession


class DummySession(ChatSession):
    def __init__(self):
        super().__init__("system prompt", {
            "api_url": "http://127.0.0.1:8080/v1/chat/completions",
            "model": "test",
            "temperature": 0.1,
            "max_tokens": 1024,
            "timeout": 10,
        })

    def send_non_streaming_request(self, payload):
        return json.dumps({"persona": "coder"})

    def complete_request(self, request):
        del request
        return ModelResponse(content=json.dumps({"persona": "coder"}))


def test_is_clearly_trivial_matches_greetings():
    assert router._is_clearly_trivial("Oi") is True
    assert router._is_clearly_trivial("Como vai?") is True
    assert router._is_clearly_trivial("qual o seu nome") is True


def test_route_objective_trivial_uses_general():
    sess = DummySession()
    persona_prompt, skills, persona = router.route_objective("Oi", sess)
    assert persona == "general"
    assert "general" in persona_prompt.lower() or "general" in skills


def test_route_objective_fallbacks_to_llm_when_not_trivial(monkeypatch):
    sess = DummySession()

    persona_prompt, skills, persona = router.route_objective("Crie um teste", sess)
    assert persona == "coder"
    assert isinstance(persona_prompt, str)
    assert isinstance(skills, list)
    assert "file_reader" in skills or "code_analyzer" in skills


def test_route_objective_handles_invalid_llm_response(monkeypatch):
    class BrokenSession(DummySession):
        def complete_request(self, request):
            del request
            return ModelResponse(content="não é json")

    sess = BrokenSession()
    persona_prompt, skills, persona = router.route_objective("Crie um teste", sess)
    assert persona == "general"
    assert "general" in persona_prompt.lower() or "general" in skills

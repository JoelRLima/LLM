"""Fast-path responses for clearly trivial objectives."""

from __future__ import annotations

from typing import Any


def answer_trivial(orchestrator: Any, objective: str) -> str:
    normalized = objective.strip().lower().rstrip("!?.")
    greetings = {"oi", "olá", "ola", "oie", "oii", "hey", "hello"}
    wellbeing = ("como vai", "tudo bem", "tudo bom", "td bem", "td bom")
    identity = (
        "quem é você",
        "o que você faz",
        "o que vc faz",
        "qual o seu nome",
        "qual seu nome",
    )
    if normalized in greetings:
        answer = "Olá! Como posso ajudar você hoje?"
    elif any(term in normalized for term in wellbeing):
        answer = "Estou bem, obrigado! Como posso ajudar você hoje?"
    elif any(term in normalized for term in identity):
        answer = (
            "Eu sou um agente de desenvolvimento assistido por IA. "
            "Posso analisar arquivos, escrever código e responder dúvidas técnicas."
        )
    else:
        answer = "Olá! Como posso ajudar você hoje?"
    orchestrator._emit("final", {"answer": answer[:100]})
    orchestrator.agent_state.conversation_history.append(
        {"user": objective, "agent": answer}
    )
    return answer

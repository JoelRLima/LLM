"""Experiment-only frozen W18 persona-routing comparison implementation."""

from __future__ import annotations

import json
import re
from typing import cast

from agent.llm.contracts import ModelMessage
from agent.llm.decision_contract import ModelRequestContract
from agent.llm.errors import ModelProviderError
from agent.llm.session import ChatSession
from agent.llm.session_requests import build_ephemeral_model_request
from agent.llm.structured_output_strategy import StructuredOutputStrategy
from agent.routing.persona.catalog import coerce_persona_id
from agent.routing.persona.contracts import PersonaId, PersonaRouteDecision, PersonaRouteRequest, PersonaRouteSource
from agent.runtime.budget import BudgetExhausted

_ROUTER_PROMPT = """You are a Router Agent.
Your job is to analyze the user's objective and decide which Agent Persona is best suited for the task.

Available Personas:
1. "coder": For writing, analyzing, modifying code, or reading the local file system / git repository.
2. "researcher": For looking up information on the web, summarizing articles, or answering general knowledge questions that require web access.
3. "general": For simple chat, math, or tasks that don't fit the above.
4. "security_auditor": For security auditing, vulnerability analysis, threat modeling, and identifying insecure code patterns.

You MUST respond ONLY with a JSON object containing the chosen persona. No extra text.
Format:
{"persona": "coder"}
"""
_SECURITY_KEYWORDS = (
    "segurança", "security", "auditoria", "audit", "vulnerabilidade", "vulnerability", "owasp", "cwe",
    "exploit", "ameaça", "threat", "command injection", "path traversal", "sandbox escape", "sandbox",
    "hardcoded", "secret", "crypto", "race condition", "auditor", "eval", "exec", "subprocess",
    "vulnerabilidades", "vulnerabilities",
)
_LISTING_KEYWORDS = frozenset({"liste", "listar", "mostrar", "mostre", "exibir", "exiba", "ls", "dir"})
_TRIVIAL_GREETINGS = frozenset({
    "oi", "olá", "ola", "bom dia", "boa tarde", "boa noite", "hey", "hi", "hello", "e aí", "e ai",
    "oie", "oii", "como vai", "como vai você", "como vc esta", "como você está", "tudo bem", "tudo bom",
    "td bem", "td bom", "quem é você", "quem e voce", "o que você faz", "o que vc faz", "o que voce faz",
    "qual o seu nome", "qual seu nome",
})
_PERSONA_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["persona"],
    "properties": {"persona": {"type": "string", "enum": [persona.value for persona in PersonaId]}},
}


def _is_clearly_trivial(objective: str) -> bool:
    clean = objective.strip().lower().rstrip("!.?")
    if clean in _TRIVIAL_GREETINGS:
        return True
    if "o que" in clean and clean not in _TRIVIAL_GREETINGS:
        return False
    words = clean.split()
    return len(words) <= 3 and any(q in clean for q in ("como vai", "tudo bem", "quem é", "o que"))


def _is_security_objective(objective: str) -> bool:
    lowered = objective.casefold()
    return any(
        (keyword in lowered if " " in keyword else re.search(rf"(?<!\w){re.escape(keyword)}(?!\w)", lowered) is not None)
        for keyword in _SECURITY_KEYWORDS
    )


def _is_listing_objective(objective: str) -> bool:
    tokens = set(re.findall(r"[^\W_]+", objective.casefold(), flags=re.UNICODE))
    return bool(tokens & _LISTING_KEYWORDS)


def _parse_model_persona(response: str) -> PersonaId | None:
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", response, flags=re.IGNORECASE)
    candidate = fenced.group(1) if fenced else response
    start = candidate.find("{")
    if start < 0:
        return None
    try:
        parsed, _ = json.JSONDecoder().raw_decode(candidate[start:])
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(parsed, dict) or set(parsed) != {"persona"}:
        return None
    return coerce_persona_id(parsed.get("persona"))


def _routing_response(session: ChatSession, objective: str) -> str:
    structured_output = StructuredOutputStrategy(session.model_profile.capabilities).select(schema=_PERSONA_SCHEMA)
    request = build_ephemeral_model_request(
        session,
        (ModelMessage(role="system", content=_ROUTER_PROMPT), ModelMessage(role="user", content=f"Objective: {objective}")),
        stream=False,
        request_contract=ModelRequestContract.PERSONA_ROUTE,
        structured_output=structured_output,
    )
    return cast(str, session.complete_request(request).content)


class W18ReferencePersonaRouter:
    """Frozen W18 routing behavior, independent of the production-current owner."""

    def __init__(self, session: ChatSession) -> None:
        self.session = session

    def route(self, request: PersonaRouteRequest) -> PersonaRouteDecision:
        if _is_clearly_trivial(request.objective):
            return PersonaRouteDecision(PersonaId.GENERAL, PersonaRouteSource.HEURISTIC_TRIVIAL)
        if _is_security_objective(request.objective):
            return PersonaRouteDecision(PersonaId.SECURITY_AUDITOR, PersonaRouteSource.HEURISTIC_SECURITY)
        if _is_listing_objective(request.objective):
            return PersonaRouteDecision(PersonaId.GENERAL, PersonaRouteSource.HEURISTIC_LISTING)
        try:
            selected = _parse_model_persona(_routing_response(self.session, request.objective))
        except (ModelProviderError, BudgetExhausted):
            raise
        except Exception as exc:
            raise ModelProviderError(str(exc), cause=exc) from exc
        if selected is None:
            return PersonaRouteDecision(PersonaId.GENERAL, PersonaRouteSource.MODEL_INVALID_FALLBACK, "PERSONA_ROUTE_INVALID_MODEL_OUTPUT")
        return PersonaRouteDecision(selected, PersonaRouteSource.MODEL)


__all__ = ["W18ReferencePersonaRouter"]

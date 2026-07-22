import json
import re
from typing import List, Tuple

from agent.llm.prompts import CODER_PROMPT, GENERAL_PROMPT, RESEARCHER_PROMPT, SECURITY_AUDITOR_PROMPT
from agent.llm.session import ChatSession
from agent.runtime.logging import logger
from agent.skills.policy import builtin_skills_for_persona

ROUTER_PROMPT = """You are a Router Agent.
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

SECURITY_KEYWORDS = [
    "segurança", "security", "auditoria", "audit", "vulnerabilidade",
    "vulnerability", "owasp", "cwe", "exploit", "ameaça", "threat",
    "command injection", "path traversal", "sandbox escape", "sandbox",
    "hardcoded", "secret", "crypto", "race condition", "auditor",
    "eval", "exec", "subprocess",
]


def is_security_objective(objective: str) -> bool:
    """Detecta se o objetivo é uma análise de segurança.

    Fonte canônica única para esta checagem — antes existiam 3-4 listas
    de keywords quase idênticas e dessincronizadas em orchestrator.py,
    final_response.py e router.py (achado crítico 1.8)."""
    obj_lower = objective.lower()
    return any(kw in obj_lower for kw in SECURITY_KEYWORDS)


TRIVIAL_GREETINGS = {
    "oi", "olá", "ola", "bom dia", "boa tarde", "boa noite",
    "hey", "hi", "hello", "e aí", "e ai", "oie", "oii",
    "como vai", "como vai você", "como vc esta", "como você está",
    "tudo bem", "tudo bom", "td bem", "td bom",
    "quem é você", "quem e voce", "o que você faz", "o que vc faz",
    "o que voce faz", "qual o seu nome", "qual seu nome"
}

def _is_clearly_trivial(objective: str) -> bool:
    clean = objective.strip().lower().rstrip("!.?")
    if clean in TRIVIAL_GREETINGS:
        return True
    words = clean.split()
    if len(words) <= 3 and any(q in clean for q in ["como vai", "tudo bem", "quem é", "o que"]):
        return True
    return False

def get_persona_config(persona: str) -> Tuple[str, List[str]]:
    if persona == "coder":
        return CODER_PROMPT, builtin_skills_for_persona("coder")
    elif persona == "researcher":
        return RESEARCHER_PROMPT, builtin_skills_for_persona("researcher")
    elif persona == "security_auditor":
        return SECURITY_AUDITOR_PROMPT, builtin_skills_for_persona("security_auditor")
    else:
        return GENERAL_PROMPT, builtin_skills_for_persona("general")

def route_objective(objective: str, session: ChatSession) -> Tuple[str, List[str]]:
    if _is_clearly_trivial(objective):
        logger.info("Router (trivial) → general")
        return get_persona_config("general")

    # Listagem simples: se o usuário só quer listar/mostrar/exibir arquivos, sem análise
    obj_lower = objective.lower()
    if any(keyword in obj_lower for keyword in ["liste", "listar", "mostrar", "mostre", "exibir", "exiba", "ls", "dir"]):
        logger.info("Router (heuristic) → general")
        return get_persona_config("general")

    # Segurança: palavras-chave de auditoria têm prioridade sobre as de código
    if is_security_objective(objective):
        logger.info("Router (keyword) → security_auditor")
        return get_persona_config("security_auditor")

    original_prompt = session.messages[0]["content"]
    session.messages[0]["content"] = ROUTER_PROMPT
    session.add_user_message(f"Objective: {objective}")

    payload = session.build_payload()
    payload["stream"] = False

    try:
        response = session.send_non_streaming_request(payload)

        match = re.search(r"```(?:json)?\s*([\s\S]*?)```", response)
        if match:
            response = match.group(1)

        start = response.find("{")
        if start != -1:
            data = json.loads(response[start:])
            persona = data.get("persona", "general").lower()
        else:
            persona = "general"
    except Exception as e:
        logger.error(f"Erro no roteamento LLM: {e}")
        persona = "general"

    session.messages[0]["content"] = original_prompt
    session.remove_last_user_message()

    if persona not in ["coder", "researcher", "general", "security_auditor"]:
        persona = "general"

    logger.info(f"Router (LLM) selecionou a persona: {persona}")
    return get_persona_config(persona)

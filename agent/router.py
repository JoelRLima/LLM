import json
import re
from typing import Tuple, List, Optional
from session import ChatSession
from logger import logger

ROUTER_PROMPT = """You are a Router Agent.
Your job is to analyze the user's objective and decide which Agent Persona is best suited for the task.

Available Personas:
1. "coder": For writing, analyzing, modifying code, or reading the local file system / git repository.
2. "researcher": For looking up information on the web, summarizing articles, or answering general knowledge questions that require web access.
3. "general": For simple chat, math, or tasks that don't fit the above.

You MUST respond ONLY with a JSON object containing the chosen persona. No extra text.
Format:
{"persona": "coder"}
"""

# Prompts enriquecidos para cada persona
PERSONA_PROMPTS = {
    "coder": """You are an Expert Software Engineer Agent.

**Core Rules for Code Analysis:**
- When asked to explore or analyze a directory:
  1. ALWAYS start with code_analyzer in 'directory' mode with compact=true to get a light-weight overview (only class and function names).
  2. After receiving the compact overview, present the main components to the user.
  3. NEVER use code_analyzer 'directory' with include_code=true for the whole directory – it will overflow the context.
  4. If the user needs detailed info on a specific file, use code_analyzer in 'file' mode with include_code=true on that single file.
  5. If you need to read a file's content, prefer file_reader with specific line ranges after consulting code_analyzer structure.

- When asked to analyze a specific file:
  1. Use code_analyzer in 'file' mode with include_code=false first to understand its structure.
  2. If the user wants implementation details, then call code_analyzer again with include_code=true or use file_reader with specific line ranges.
  3. Summarize concisely in Portuguese.

- General coding assistance:
  - Use python_executor for calculations, data processing, or quick code snippets.
  - Use grep to search for patterns across files.
  - Use git for version control queries.
  - Use session_memory to remember important findings automatically.

- Always provide clear, well-structured answers in Portuguese (Brazil).""",

    "researcher": """You are an Expert Researcher Agent.

**Core Rules for Research:**
- When asked to search the web:
  1. Use web_search with appropriate search terms.
  2. After receiving results, summarize the key findings in Portuguese.
  3. Cite your sources when available.

- When asked to summarize long texts:
  - Use summarize to condense information.
  - Present the summary in clear, structured Portuguese.

- General knowledge questions:
  - If you can answer from your training, do so directly.
  - If you need current or external information, use web_search first.

- Use session_memory to store research findings for later reference.""",

    "general": """You are a General Assistant Agent.

**Core Rules for General Interaction:**
- For casual conversation, greetings, or simple questions:
  - Respond directly in Portuguese in a friendly, helpful manner.
  - Do NOT use any tools unless absolutely necessary.

- For simple math or conversions:
  - If it's trivial, answer directly.
  - If it requires computation, use python_executor.

- You have access to tools, but only use them when the user's request clearly requires them.
- Keep responses natural and conversational.

- **CRITICAL**: For ALL responses, including simple greetings, you MUST respond ONLY in JSON format. No extra text.
  Format: {"action":"final","answer":"<your response in Portuguese>"}
"""
}

# Conjunto de entradas que não precisam de classificação via LLM (triviais)
TRIVIAL_GREETINGS = {
    "oi", "olá", "ola", "bom dia", "boa tarde", "boa noite",
    "hey", "hi", "hello", "e aí", "e ai", "oie", "oii",
    "como vai", "como vai você", "como vc esta", "como você está",
    "tudo bem", "tudo bom", "td bem", "td bom",
    "quem é você", "quem e voce", "o que você faz", "o que vc faz",
    "o que voce faz", "qual o seu nome", "qual seu nome"
}

def _is_clearly_trivial(objective: str) -> bool:
    """Heurística para detectar tarefas que não precisam de classificação do LLM."""
    clean = objective.strip().lower().rstrip("!.?")
    # Saudação ou pergunta genérica muito curta
    if clean in TRIVIAL_GREETINGS:
        return True
    # Frases com até 3 palavras que contenham perguntas simples
    words = clean.split()
    if len(words) <= 3 and any(q in clean for q in ["como vai", "tudo bem", "quem é", "o que"]):
        return True
    return False

def get_persona_config(persona: str) -> Tuple[str, List[str]]:
    """Retorna o system prompt enriquecido e a lista de skills permitidas para a persona."""
    if persona == "coder":
        return (PERSONA_PROMPTS["coder"],
                ["file_reader", "file_writer", "shell", "directory_lister",
                 "code_analyzer", "grep", "python_executor", "git", "session_memory"])
    elif persona == "researcher":
        return (PERSONA_PROMPTS["researcher"],
                ["web_search", "summarize", "session_memory"])
    else:
        return (PERSONA_PROMPTS["general"],
                ["session_memory", "summarize", "calculator"])

def route_objective(objective: str, session: ChatSession) -> Tuple[str, List[str]]:
    """
    Decide qual persona assumir baseado no objetivo.
    1. Entradas triviais → general sem chamar o LLM.
    2. Palavras-chave de código/pesquisa → persona correspondente.
    3. Caso contrário, consulta o LLM.
    """
    # ------------------------------------------------------------
    # 1. Triviais: saudação ou pergunta genérica curta
    # ------------------------------------------------------------
    if _is_clearly_trivial(objective):
        logger.info("Router (trivial) → general")
        return get_persona_config("general")

    # ------------------------------------------------------------
    # 2. Palavras-chave determinísticas
    # ------------------------------------------------------------
    projeto_keywords = [
        "estrutura", "projeto", "arquivo", "diretório", "código", "repo",
        "file", "directory", "project", "code", "skill", "agente",
        "analise", "analisar", "ler", "criar", "escrever", "modificar",
        "commit", "git", "python", "script", "módulo", "classe", "função"
    ]
    pesquisa_keywords = [
        "pesquisar", "buscar", "notícia", "artigo", "web", "internet",
        "pesquisa", "busca", "search", "news", "atualidade", "hoje"
    ]

    if any(kw in objective.lower() for kw in projeto_keywords):
        logger.info("Router (keyword) → coder")
        return get_persona_config("coder")

    if any(kw in objective.lower() for kw in pesquisa_keywords):
        logger.info("Router (keyword) → researcher")
        return get_persona_config("researcher")

    # ------------------------------------------------------------
    # 3. Classificação via LLM
    # ------------------------------------------------------------
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
        logger.error(f"Erro no roteamento: {e}")
        persona = "general"
        
    session.messages[0]["content"] = original_prompt
    session.remove_last_user_message()
    
    if persona not in ["coder", "researcher", "general"]:
        persona = "general"
        
    logger.info(f"Router (LLM) selecionou a persona: {persona}")
    return get_persona_config(persona)
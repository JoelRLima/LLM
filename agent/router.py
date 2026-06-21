import json
import re
from typing import Tuple, List
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

PERSONA_PROMPTS = {
    "coder": """You are an Expert Software Engineer Agent.

**Core Rules for Code Analysis:**
- When asked to analyze a specific file:
  1. Use code_analyzer with mode='file' and compact=true to get the list of functions/classes.
  2. Read the file content with file_reader (informe apenas o file_path).
  3. For modifications, prefer file_writer with action='ast_patch' (functions/classes) or action='patch' (text lines).
  4. Only use action='write' to create a new file or replace the entire content.

- When asked to explore a directory: use code_analyzer with mode='directory' and compact=true.
- NEVER use code_analyzer with include_code=true.
- General coding: use python_executor, grep, git, session_memory.
- Always respond in Portuguese (Brazil).
""",

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

- For simple requests to list, show, or display files/directories:
  - Use directory_lister to get the items.
  - Present the list in a clean, readable format (ex.: bullet points or a simple table).
  - Do NOT add analysis, suggestions, or extra commentary. Just show what was requested.

- For simple math or conversions:
  - If it's trivial, answer directly.
  - If it requires computation, use python_executor.

- You have access to tools, but only use them when the user's request clearly requires them.
- Keep responses natural and conversational.

- **CRITICAL**: For ALL responses, including simple greetings, you MUST respond ONLY in JSON format. No extra text.
  Format: {"action":"final","answer":"<your response in Portuguese>"}
"""
}

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
        return (PERSONA_PROMPTS["coder"],
                ["file_reader", "file_writer", "shell", "directory_lister",
                 "code_analyzer", "grep", "python_executor", "git", "session_memory"])
    elif persona == "researcher":
        return (PERSONA_PROMPTS["researcher"],
                ["web_search", "summarize", "session_memory"])
    else:
        return (PERSONA_PROMPTS["general"],
        ["session_memory", "summarize", "python_executor", "directory_lister"])

def route_objective(objective: str, session: ChatSession) -> Tuple[str, List[str]]:
    if _is_clearly_trivial(objective):
        logger.info("Router (trivial) → general")
        return get_persona_config("general")

    # Listagem simples: se o usuário só quer listar/mostrar/exibir arquivos, sem análise
    listagem_keywords = ["liste", "listar", "mostrar", "mostre", "exibir", "exiba", "ls", "dir"]
    analise_keywords = ["analise", "analisar", "melhoria", "sugestão", "problema", "bug",
                        "corrigir", "corrige", "otimizar", "refatorar", "comparar"]
    obj_lower = objective.lower()
    if any(kw in obj_lower for kw in listagem_keywords) and not any(kw in obj_lower for kw in analise_keywords):
        logger.info("Router (listagem simples) → general")
        return get_persona_config("general")

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
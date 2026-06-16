import json
import re
import os
import sys
from typing import Any, Dict, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from session import ChatSession

AGENT_SYSTEM_PROMPT = """You are a strict execution agent.

You MUST respond ONLY with JSON.

No explanations. No text. No markdown.

You decide ONLY between:
- tool execution
- final answer

Rules:
- NEVER describe progress
- NEVER say partial / maybe / almost
- NEVER ask questions
- NEVER justify

Output format:

Tool:
{{"action":"tool","tool":"<tool_name>","args":{{...}}}}

Final:
{{"action":"final","answer":"<answer in Portuguese>"}}

Available tools:
{tools_description}

- Use the tool session_memory to remember important information during the session.
- Prefer using code_analyzer in 'directory' mode when analyzing multiple files.
- When asked to analyze a specific file, first use code_analyzer on that file to get its structure. Then, if you need more details, use file_reader with specific line ranges based on what you found, not arbitrary intervals. Provide a concise summary of the file's purpose, key components, and how they connect.
- When analyzing a file, use code_analyzer with include_code=true to get the full content immediately. Avoid file_reader unless you need specific line ranges that are not already covered by code_analyzer.
- After reading 3-4 sections of the same file, you MUST stop and produce a final answer. Do not re-read sections you already have. If you obtain the complete file (total_lines equals the range you read), immediately give your analysis without any further tool calls.

Tool/agent contract:
- Tools MUST return a JSON object with:
  - ok: boolean
  - done: boolean
  - data: any (nullable)
  - error: string (nullable)
  - message: string (nullable)
- If ok=false, done must be false.
- The agent may only emit final when the last tool result has ok=true and done=true.
- However, if NO tool has been called yet, the agent may emit final directly if the task is trivial (e.g., greeting).
"""

ERROR_PATTERNS = [
    "erro", "falha", "exception", "não encontrado", "not found",
    "timeout", "permissão negada", "access denied", "invalid",
    "inválido", "sem resultado", "no result",
]

class Orchestrator:
    def __init__(self, session: ChatSession, skills: list = None, verbose: bool = False):
        self.session = session
        self.skills = {}
        self.max_steps = 15
        self.max_total_actions = 20
        self.max_early_final_attempts = 3
        self.max_loop_repetitions = 3
        self.verbose = verbose          # agora controla apenas detalhes técnicos

        self.state = {
            "objective": None,
            "last_result": None,
            "last_tool": None,
            "last_args": None,
            "step": 0,
            "tool_history": []
        }

        self.memory = {
            "project_map": {},
            "key_findings": {},
            "files_index": {},
            "todo": [],
            "notes": {}
        }

        if skills:
            for s in skills:
                self.register_skill(s)

    # ---- Skills ----
    def register_skill(self, skill):
        self.skills[skill.name] = skill

    def unregister_skill(self, name):
        self.skills.pop(name, None)

    def _build_tools_description(self):
        out = []
        for s in self.skills.values():
            out.append(f"- {s.name}: {s.description}\nArgs: {json.dumps(s.get_schema(), indent=2, ensure_ascii=False)}")
        return "\n".join(out)

    # ---- JSON parser ----
    def _extract_json(self, text: str) -> Optional[dict]:
        if not text:
            return None
        cleaned = re.sub(r"```(?:json)?\s*|\s*```", "", text)
        start = cleaned.find("{")
        if start == -1:
            return None
        balance = 0
        in_string = False
        escape = False
        end = -1
        for i in range(start, len(cleaned)):
            c = cleaned[i]
            if escape:
                escape = False
                continue
            if c == '\\':
                escape = True
                continue
            if c == '"':
                in_string = not in_string
                continue
            if not in_string:
                if c == '{':
                    balance += 1
                elif c == '}':
                    balance -= 1
                    if balance == 0:
                        end = i
                        break
        if end == -1:
            return None
        json_str = cleaned[start:end + 1]
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            return None

    def _stringify(self, obj: Any) -> str:
        try:
            return json.dumps(obj, ensure_ascii=False, indent=2, default=str)
        except Exception:
            return str(obj)

    # ---- Memory management ----
    def remember(self, key: str, value: Any, section: str = "key_findings"):
        if section in self.memory and isinstance(self.memory[section], dict):
            self.memory[section][key] = value
        else:
            self.memory[key] = value

    def forget(self, key: str):
        self.memory.pop(key, None)

    def clear_memory(self):
        self.memory.clear()
    
    def save_memory_to_file(self, path: str = "agent_memory.json") -> str:
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.memory, f, ensure_ascii=False, indent=2)
            return f"Memória salva em {path}."
        except Exception as e:
            return f"Erro ao salvar memória: {e}"

    def load_memory_from_file(self, path: str = "agent_memory.json") -> str:
        try:
            with open(path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            for section in self.memory:
                if section in loaded:
                    if isinstance(self.memory[section], dict):
                        self.memory[section].update(loaded[section])
                    elif isinstance(self.memory[section], list):
                        self.memory[section].extend(loaded[section])
            return f"Memória carregada de {path}."
        except FileNotFoundError:
            return f"Arquivo {path} não encontrado."
        except Exception as e:
            return f"Erro ao carregar memória: {e}"

    # ---- Decision validation ----
    def _validate_decision(self, decision) -> Tuple[bool, Optional[str]]:
        if not isinstance(decision, dict):
            return False, "Decisão não é um dicionário."
        action = decision.get("action")
        if action not in ("tool", "final"):
            return False, f"Ação inválida: {action}"
        if action == "tool":
            if "tool" not in decision:
                return False, "Falta o campo 'tool'."
            if not isinstance(decision.get("tool"), str) or not decision.get("tool").strip():
                return False, "'tool' deve ser uma string não vazia."
            args = decision.get("args", {})
            if args is not None and not isinstance(args, dict):
                return False, "'args' deve ser um dicionário."
        if action == "final":
            if "answer" not in decision:
                return False, "Falta o campo 'answer'."
            if not isinstance(decision.get("answer"), str):
                return False, "'answer' deve ser uma string."
        return True, None

    # ---- Tool result contract ----
    def _normalize_tool_result(self, result: Any) -> Dict[str, Any]:
        if isinstance(result, dict):
            ok = result.get("ok") is True
            done = result.get("done") is True
            if not ok:
                done = False
            normalized = {"ok": ok, "done": done, "data": result.get("data"), "error": result.get("error"), "message": result.get("message")}
            for k, v in result.items():
                if k not in normalized:
                    normalized[k] = v
            return normalized
        if result is None:
            return {"ok": False, "done": False, "data": None, "error": "Tool retornou None.", "message": "Retorno vazio da ferramenta."}
        if isinstance(result, str):
            lower = result.strip().lower()
            if any(pattern in lower for pattern in ERROR_PATTERNS):
                return {"ok": False, "done": False, "data": None, "error": result, "message": "A ferramenta retornou uma mensagem de erro."}
            return {"ok": True, "done": True, "data": result, "error": None, "message": None}
        return {"ok": True, "done": True, "data": result, "error": None, "message": None}

    def _is_task_solved(self) -> bool:
        if not self.state["tool_history"]:
            return True
        r = self.state["last_result"]
        if not isinstance(r, dict):
            return False
        return r.get("ok") is True and r.get("done") is True

    # ---- Model call (injeta memória) ----
    def _ask_model(self, prompt: str) -> Dict[str, Any]:
        original = self.session.messages[0]["content"]

        memory_context = ""
        if self.memory:
            memory_context = "\n\n--- SESSION MEMORY (important context) ---\n" + self._stringify(self.memory)

        self.session.messages[0]["content"] = AGENT_SYSTEM_PROMPT.format(
            tools_description=self._build_tools_description()
        ) + memory_context

        self.session.add_user_message(prompt)
        payload = self.session.build_payload()
        payload["max_tokens"] = self.session.config.get("agent_max_tokens", 8192)
        payload["stream"] = False

        if self.verbose:
            print("⏳ Consultando o modelo...", end="", flush=True)
        try:
            response = self.session.send_non_streaming_request(payload)
        except Exception as e:
            response = f"Erro na requisição: {e}"
        if self.verbose:
            print(" ✓")

        self.session.messages[0]["content"] = original
        self.session.remove_last_user_message()

        if self.verbose:
            print(f"[DEBUG] Resposta bruta: {str(response)[:300]}")

        decision = self._extract_json(response)
        if decision is not None:
            return decision
        return {"action": "error", "message": "Falha ao extrair JSON da resposta.", "raw_response": str(response)}

    # ---- Tool execution ----
    def _run_tool(self, tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        if tool_name not in self.skills:
            result = {"ok": False, "done": False, "data": None, "error": f"Tool '{tool_name}' não existe.", "message": None}
        else:
            # Mensagem narrativa (sempre visível)
            print(f"⚙️  Usando {tool_name}...", end="", flush=True)
            try:
                raw_result = self.skills[tool_name].execute(args)
            except Exception as e:
                raw_result = {"ok": False, "done": False, "data": None, "error": f"Erro ao executar tool: {e}", "message": "Exceção durante a execução da ferramenta."}
            result = self._normalize_tool_result(raw_result)

        # Mostra resultado amigável (sempre)
        msg = result.get("message") or ("Concluído" if result.get("ok") else "Falha")
        print(f" {msg}")

        # Detalhes técnicos (apenas verbose)
        if self.verbose:
            print(f"[DEBUG] Resultado completo: {self._stringify(result)}")

        self.state["last_tool"] = tool_name
        self.state["last_args"] = args
        self.state["last_result"] = result
        self.state["tool_history"].append({"tool": tool_name, "args": args, "result": result})
        return result

    # ---- Main loop ----
    def run(self, objective: str):
        original_msg_count = len(self.session.messages)
        tool_usage_count = {}

        try:
            self.state["objective"] = objective
            self.state["step"] = 0
            self.state["last_result"] = None
            self.state["last_tool"] = None
            self.state["last_args"] = None
            self.state["tool_history"] = []

            early_final_count = 0
            loop_count = 0
            total_actions = 0

            # Narrativa de início
            print(f"\n🤖 Analisando: \"{objective}\"")

            prompt = objective

            while self.state["step"] < self.max_steps:
                self.state["step"] += 1
                total_actions += 1

                if total_actions > self.max_total_actions:
                    print("⚠️ Limite de ações atingido. Encerrando.")
                    last = self.state.get("last_result", {})
                    return f"Tarefa não resolvida no limite de ações. Último resultado: {self._stringify(last)}"

                decision = self._ask_model(prompt)

                if decision.get("action") == "error":
                    print(f"❌ Erro ao interpretar resposta do modelo.")
                    if self.verbose:
                        print(f"[DEBUG] {decision.get('message')}")
                    prompt = "Sua última resposta não foi um JSON válido. Responda apenas com JSON no formato exigido."
                    continue

                valid, error_msg = self._validate_decision(decision)
                if not valid:
                    print(f"❌ Resposta inválida do modelo.")
                    if self.verbose:
                        print(f"[DEBUG] {error_msg}")
                    prompt = f"Resposta inválida. Reenvie no formato JSON correto."
                    continue

                action = decision["action"]

                # FINAL
                if action == "final":
                    if self._is_task_solved():
                        answer = decision.get("answer", "")
                        if self.verbose:
                            print(f"[DEBUG] Final aceito: {answer[:100]}...")
                        return answer

                    early_final_count += 1
                    if early_final_count >= self.max_early_final_attempts:
                        print("⚠️ Tentativas excessivas de finalizar. Encerrando.")
                        last = self.state["last_result"] or {}
                        return decision.get("answer") or f"Tarefa não resolvida. Último resultado: {self._stringify(last)}"

                    if self.verbose:
                        print(f"[DEBUG] Tentativa de final precoce ({early_final_count}/{self.max_early_final_attempts}).")
                    prompt = (
                        f"OBJETIVO: {objective}\n\n"
                        f"ÚLTIMO RESULTADO DA TOOL: {self._stringify(self.state['last_result'])}\n\n"
                        "A tarefa NÃO está resolvida. Você DEVE usar uma ferramenta agora. Não retorne 'final'."
                    )
                    continue

                # TOOL
                if action == "tool":
                    early_final_count = 0
                    tool = decision["tool"]
                    args = decision.get("args", {})
                    if not isinstance(args, dict):
                        args = {}

                    # Proteção contra loops consecutivos
                    if tool == self.state["last_tool"] and args == self.state["last_args"]:
                        loop_count += 1
                        if loop_count >= self.max_loop_repetitions:
                            print("⚠️ Loop de ferramenta detectado. Encerrando.")
                            return f"Loop de ferramenta ({tool}). Tarefa interrompida."
                    else:
                        loop_count = 0

                    # Proteção contra repetições não consecutivas
                    usage_key = json.dumps((tool, args), sort_keys=True, default=str)
                    tool_usage_count[usage_key] = tool_usage_count.get(usage_key, 0) + 1
                    if tool_usage_count[usage_key] > self.max_loop_repetitions:
                        print("⚠️ Ferramenta já usada muitas vezes com os mesmos argumentos. Encerrando.")
                        return f"Repetição excessiva da ferramenta {tool}."

                    result = self._run_tool(tool, args)

                    prompt = (
                        f"OBJETIVO: {objective}\n\n"
                        f"ÚLTIMA FERRAMENTA: {tool}\n"
                        f"ARGUMENTOS: {self._stringify(args)}\n"
                        f"RESULTADO DA TOOL: {self._stringify(result)}\n\n"
                        "Decida: usar outra ferramenta ou retornar 'final' apenas se a última tool tiver ok=true e done=true."
                    )
                    continue

                print(f"❌ Ação desconhecida: {action}")
                break

            return "Número máximo de passos atingido."

        finally:
            while len(self.session.messages) > original_msg_count:
                self.session.messages.pop()
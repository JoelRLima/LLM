import json
import os
import re
import sys
from typing import Any, Dict, Optional, Tuple, List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from session import ChatSession
from logger import logger
from agent.prompts import AGENT_SYSTEM_PROMPT, ERROR_PATTERNS
from agent.parsers import extract_json, stringify, validate_decision, normalize_tool_result, extract_json_from_end
from agent.state import AgentState
from agent.router import route_objective, _is_clearly_trivial

# ----------------------------------------------------------------------
# Orçamento de tokens para decisões do agente (por tipo de passo)
# ----------------------------------------------------------------------
DEFAULT_AGENT_MAX_TOKENS = 2048
FALLBACK_AGENT_MAX_TOKENS = 4096

STEP_BUDGETS = {
    "plan": 4096,
    "final": 4096,
    "tool_decision": 2048,   # decisão intermediária sem ferramenta específica
}

# Budgets individuais para ferramentas (referência / uso futuro)
TOOL_DECISION_BUDGETS = {
    "file_writer": 1024,
    "python_executor": 512,
    "shell": 256,
    "grep": 150,
    "code_analyzer": 150,
    "file_reader": 150,
    "directory_lister": 150,
    "session_memory": 150,
    "summarize": 300,
    "web_search": 200,
    "git": 200,
    "echo": 100,
    "calculator": 100,
}


class Orchestrator:
    def __init__(self, session: ChatSession, skills: Optional[List[Any]] = None, verbose: bool = False) -> None:
        self.session = session
        self.skills: Dict[str, Any] = {}
        self.max_steps: int = 15
        self.max_total_actions: int = 20
        self.max_early_final_attempts: int = 3
        self.max_loop_repetitions: int = 3
        self.verbose: bool = verbose
        self.active_skills: List[str] = []
        self._cached_project_context: Optional[str] = None   # cache para contexto do projeto

        self.agent_state = AgentState()

        if skills:
            for s in skills:
                self.register_skill(s)

    def register_skill(self, skill: Any) -> None:
        self.skills[skill.name] = skill

    def unregister_skill(self, name: str) -> None:
        self.skills.pop(name, None)

    def _build_tools_description(self, compact: bool = False) -> str:
        out = []
        for s in self.skills.values():
            if not self.active_skills or s.name in self.active_skills:
                if compact:
                    out.append(f"- {s.name}: {s.description}")
                else:
                    out.append(f"- {s.name}: {s.description}\nArgs: {json.dumps(s.get_schema(), indent=2, ensure_ascii=False)}")
        return "\n".join(out)

    def remember(self, key: str, value: Any, section: str = "key_findings") -> None:
        self.agent_state.memory.remember(key, value, section)

    def forget(self, key: str) -> None:
        self.agent_state.memory.forget(key)

    def clear_memory(self) -> None:
        self.agent_state.memory.clear()
        self.agent_state.events.clear()

    def save_memory_to_file(self, path: str = "agent_memory.json") -> str:
        return self.agent_state.memory.save_to_file(path)

    def load_memory_from_file(self, path: str = "agent_memory.json") -> str:
        return self.agent_state.memory.load_from_file(path)

    def _emit(self, event_type: str, data: Dict[str, Any] = None) -> None:
        event = {
            "type": event_type,
            "step": self.agent_state.objective is not None,
            "data": data or {}
        }
        self.agent_state.events.append(event)
        if self.verbose:
            emoji = {
                "plan_created": "📋",
                "tool_start": "⚙️",
                "tool_end": "✅",
                "final": "💬",
                "error": "❌",
                "hard_block": "🚫",
                "loop_detected": "🔄"
            }.get(event_type, "•")
            print(f"{emoji} [{event_type}] {data}")

    def _maybe_summarize_and_store(self, tool_name: str, args: Dict[str, Any], result: Dict[str, Any]) -> None:
        if tool_name in ("code_analyzer", "file_reader") and result.get("ok"):
            file_path = args.get("target") or args.get("file_path")
            if file_path and "data" in result:
                content = result.get("data")
                if isinstance(content, dict):
                    if not content.get("classes") and not content.get("functions"):
                        return
                    content = stringify(content)
                if content and len(str(content)) > 300:
                    summary = self._summarize_text(str(content), context=f"Arquivo: {file_path}")
                    self.agent_state.memory.state["analyzed_files"][file_path] = summary[:150]
                    self.agent_state.memory.state["file_summaries"][file_path] = summary

    def _summarize_text(self, text: str, context: str = "") -> str:
        try:
            summarize_skill = self.skills.get("summarize")
            if summarize_skill:
                result = summarize_skill.execute({"text": text, "context": context})
                if result.get("ok"):
                    return result.get("data", text[:300])
        except Exception as e:
            logger.warning(f"Falha ao usar summarize_skill: {e}")
        return text[:300] + "..." if len(text) > 300 else text

    def _is_task_solved(self) -> bool:
        if not self.agent_state.tool_history:
            return True
        r = self.agent_state.last_result
        if not isinstance(r, dict):
            return False
        return r.get("ok") is True and r.get("done") is True

    # ------------------------------------------------------------------
    # Contexto do projeto com cache (calculado uma vez por execução)
    # ------------------------------------------------------------------
    def _get_project_context(self) -> str:
        """Retorna o contexto do projeto, computando apenas uma vez."""
        if self._cached_project_context is not None:
            return self._cached_project_context

        ctx = ""
        try:
            import subprocess
            result = subprocess.run(
                ["git", "ls-files", "--others", "--cached", "--exclude-standard"],
                capture_output=True, text=True, timeout=5, cwd=os.getcwd()
            )
            if result.returncode == 0 and result.stdout.strip():
                files = result.stdout.strip().splitlines()[:50]
                file_list = "\n".join(f"  {f}" for f in files)
                ctx = f"\n\n--- CONTEXTO DO PROJETO ---\nArquivos rastreados pelo Git ({len(files)} arquivos):\n{file_list}\n"
        except Exception:
            pass

        if not ctx:
            try:
                root = os.getcwd()
                entries = []
                for item in sorted(os.listdir(root)):
                    if item.startswith(".") or item == "__pycache__":
                        continue
                    full = os.path.join(root, item)
                    tag = "/" if os.path.isdir(full) else ""
                    entries.append(f"  {item}{tag}")
                ctx = f"\n\n--- CONTEXTO DO PROJETO ---\nEstrutura raiz:\n" + "\n".join(entries[:40]) + "\n"
            except Exception:
                pass

        self._cached_project_context = ctx
        return ctx

    # ------------------------------------------------------------------
    # Descoberta do tamanho de arquivos mencionados no objetivo
    # ------------------------------------------------------------------
    def _get_file_hints(self, objective: str) -> str:
        """
        Retorna uma string com o número de linhas de arquivos que parecem
        ser alvo do objetivo. Não usa ferramentas nem LLM.
        """
        candidates = re.findall(r'\b[\w\-.]+\.(?:py|md|txt|json|yaml|yml|toml|cfg)\b', objective)
        hints = []
        seen = set()
        for fname in candidates:
            if fname in seen:
                continue
            seen.add(fname)
            path = os.path.join(os.getcwd(), fname)
            if os.path.isfile(path):
                try:
                    with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                        line_count = sum(1 for _ in f)
                    hints.append(f"{fname} ({line_count} linhas)")
                except Exception:
                    pass
        if hints:
            return "\n".join(f"- {h}" for h in hints)
        return ""

    # ------------------------------------------------------------------
    # Comunicação com o modelo (com budget variável por step_type)
    # ------------------------------------------------------------------
    def _ask_model(self, prompt: str, step_type: str = "tool_decision") -> Dict[str, Any]:
        # Salva estado original da sessão
        original_messages = [m.copy() for m in self.session.messages]
        original_system_content = self.session.messages[0]["content"]

        try:
            # --- Montagem do contexto (igual antes) ---
            analyzed_context = ""
            if self.agent_state.memory.state.get("analyzed_files"):
                analyzed_context = "\n\n--- ARQUIVOS JÁ ANALISADOS ---\n"
                for file, summary in self.agent_state.memory.state["analyzed_files"].items():
                    analyzed_context += f"- {file}: {summary}\n"
                analyzed_context += "NÃO reanalise arquivos já listados aqui, a menos que o usuário peça explicitamente.\n"

            memory_context = ""
            if self.agent_state.memory.state:
                memory_context = "\n\n--- SESSION MEMORY ---\n" + self.agent_state.memory.stringify()
            memory_context += analyzed_context

            history_context = ""
            if self.agent_state.conversation_history:
                turns = self.agent_state.conversation_history[-self.agent_state.max_history_turns:]
                history_context = "\n\n--- HISTÓRICO RECENTE ---\n"
                for turn in turns:
                    history_context += f"Usuário: {turn['user']}\nAgente: {turn['agent']}\n\n"

            persona_prefix = getattr(self, "current_persona_prompt", "")

            from datetime import datetime
            now_str = datetime.now().strftime("%A, %d de %B de %Y %H:%M")
            datetime_context = f"\n\n[SISTEMA] Data e hora atual: {now_str}. Use esta informação para responder perguntas sobre datas."

            project_context = self._get_project_context()

            # Monta o system prompt completo
            self.session.messages[0]["content"] = (
                persona_prefix + "\n\n"
                + AGENT_SYSTEM_PROMPT.format(tools_description=self._build_tools_description())
                + datetime_context
                + project_context
                + history_context
                + memory_context
            )

            # Adiciona a mensagem de usuário
            self.session.add_user_message(prompt)
            payload = self.session.build_payload()

            # Define budget
            config_max = self.session.config.get("agent_max_tokens")
            if config_max is not None:
                budget = config_max
            else:
                budget = STEP_BUDGETS.get(step_type, DEFAULT_AGENT_MAX_TOKENS)

            payload["max_tokens"] = budget
            payload["stream"] = False

            if self.verbose:
                print(f"⏳ Consultando o modelo (step={step_type}, budget={budget})...", end="", flush=True)

            # Primeira tentativa
            try:
                response = self.session.send_non_streaming_request(payload)
            except Exception as e:
                logger.error(f"Erro na requisição ao modelo: {e}")
                response = f"Erro na requisição: {e}"

            if self.verbose:
                print(" ✓")
                print(f"[DEBUG] Resposta bruta: {str(response)[:300]}")

            decision = extract_json(response)
            if decision is None:
                decision = extract_json_from_end(response)
            if decision is not None:
                return decision

            # Retry com mais tokens
            if self.verbose:
                print("[DEBUG] Resposta possivelmente truncada. Retentando com mais tokens...", end="", flush=True)
            retry_payload = self.session.build_payload()
            retry_payload["max_tokens"] = self.session.config.get("agent_max_tokens", FALLBACK_AGENT_MAX_TOKENS)
            retry_payload["stream"] = False
            try:
                retry_response = self.session.send_non_streaming_request(retry_payload)
            except Exception as e:
                logger.error(f"Erro no retry: {e}")
                retry_response = f"Erro na requisição: {e}"
            if self.verbose:
                print(" ✓")
            decision = extract_json(retry_response)
            if decision is not None:
                return decision

            return {"action": "error", "message": "Falha ao extrair JSON da resposta.", "raw_response": str(response)}

        finally:
            # Restaura o estado original da sessão, aconteça o que acontecer
            self.session.messages = original_messages
            self.session.messages[0]["content"] = original_system_content

    def _run_tool(self, tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        # Bloqueia qualquer tentativa de esvaziar o arquivo de notas
        if tool_name == "file_writer":
            file_path = args.get("file_path", "")
            content = args.get("content", "")
            action = args.get("action", "write")
            if file_path == "analysis_notes.md" and action == "write" and content.strip() == "":
                self._emit("hard_block", {"file": file_path, "reason": "tentativa de esvaziar analysis_notes.md"})
                return {"ok": False, "done": False, "data": None, "error": "Operação bloqueada: não é permitido esvaziar o arquivo de notas.", "message": "Operação bloqueada."}
        if tool_name not in self.skills or (self.active_skills and tool_name not in self.active_skills):
            result = {"ok": False, "done": False, "data": None, "error": f"Tool '{tool_name}' não existe ou não está permitida para esta persona.", "message": None}
        else:
            print(f"⚙️  Usando {tool_name}...", end="", flush=True)
            logger.info(f"Executando tool {tool_name} com args {args}")
            try:
                raw_result = self.skills[tool_name].execute(args)
            except Exception as e:
                logger.error(f"Erro ao executar tool {tool_name}: {e}", exc_info=True)
                raw_result = {"ok": False, "done": False, "data": None, "error": f"Erro ao executar tool: {e}", "message": "Exceção durante a execução da ferramenta."}
            result = normalize_tool_result(raw_result, ERROR_PATTERNS)

        msg = result.get("message") or ("Concluído" if result.get("ok") else "Falha")
        print(f" {msg}")

        if self.verbose:
            print(f"[DEBUG] Resultado completo: {stringify(result)}")

        self.agent_state.last_tool = tool_name
        self.agent_state.last_args = args
        self.agent_state.last_result = result
        self.agent_state.tool_history.append({"tool": tool_name, "args": args, "result": result})
        return result

    # ==================================================================
    # Fallback reativo (modo antigo) para quando o plano não é gerado
    # ==================================================================
    def _run_reactive(self, objective: str, tool_usage_count: Dict[str, int], original_msg_count: int) -> str:
        prompt = objective
        error_streak = 0
        early_final_count = 0
        loop_count = 0
        total_actions = 0

        while self.agent_state.plan_step < self.max_steps:
            self.agent_state.plan_step += 1
            total_actions += 1

            if total_actions > self.max_total_actions:
                print("⚠️ Limite de ações atingido. Encerrando.")
                last = self.agent_state.last_result or {}
                ans = f"Tarefa não resolvida no limite de ações. Último resultado: {stringify(last)}"
                self.agent_state.conversation_history.append({"user": objective, "agent": ans})
                return ans

            decision = self._ask_model(prompt, step_type="tool_decision")

            if decision.get("action") == "error":
                error_streak += 1
                if error_streak >= 3:
                    print("⚠️ Muitas falhas consecutivas. Encerrando.")
                    ans = f"Erro persistente: {decision.get('message')}"
                    self.agent_state.conversation_history.append({"user": objective, "agent": ans})
                    return ans
                if self.verbose:
                    print(f"[DEBUG] Erro ao interpretar resposta: {decision.get('message')}")
                prompt = f"Responda APENAS com o JSON exigido. Objetivo: {objective}"
                continue
            else:
                error_streak = 0

            valid, error_msg = validate_decision(decision)
            if not valid:
                if self.verbose:
                    print(f"[DEBUG] Decisão inválida: {error_msg}")
                prompt = f"Sua última resposta foi inválida: {error_msg}. Corrija e reenvie APENAS o JSON no formato correto."
                continue

            action = decision["action"]

            if action == "final":
                if self._is_task_solved():
                    answer = decision.get("answer", "")
                    self._emit("final", {"answer": answer[:100]})
                    self.agent_state.conversation_history.append({"user": objective, "agent": answer})
                    return answer

                early_final_count += 1
                if early_final_count >= self.max_early_final_attempts:
                    print("⚠️ Tentativas excessivas de finalizar. Encerrando.")
                    last = self.agent_state.last_result or {}
                    ans = decision.get("answer") or f"Tarefa não resolvida. Último resultado: {stringify(last)}"
                    self.agent_state.conversation_history.append({"user": objective, "agent": ans})
                    return ans

                if self.verbose:
                    print(f"[DEBUG] Tentativa de final precoce ({early_final_count}/{self.max_early_final_attempts}).")
                prompt = (
                    f"OBJETIVO: {objective}\n\n"
                    f"ÚLTIMO RESULTADO DA TOOL: {stringify(self.agent_state.last_result)}\n\n"
                    "A tarefa NÃO está resolvida. Você DEVE usar uma ferramenta agora. Não retorne 'final'."
                )
                continue

            if action == "tool":
                early_final_count = 0
                tool = decision["tool"]
                args = decision.get("args", {})
                if not isinstance(args, dict):
                    args = {}

                file_path = args.get("target") or args.get("file_path")

                # 🚫 Bloqueia code_analyzer repetido para o mesmo arquivo
                if tool == "code_analyzer" and file_path:
                    code_analyzer_key = f"code_analyzer_{file_path}"
                    tool_usage_count[code_analyzer_key] = tool_usage_count.get(code_analyzer_key, 0) + 1
                    if tool_usage_count[code_analyzer_key] > 1:
                        self._emit("hard_block", {"file": file_path, "reason": "code_analyzer repetido"})
                        if self.verbose:
                            print(f"[DEBUG] Bloqueada reanálise estrutural de {file_path}. Use file_reader para detalhes.")
                        prompt = (
                            f"O arquivo '{file_path}' já teve sua estrutura analisada. "
                            f"NÃO use code_analyzer novamente. Use file_reader com start_line e end_line para detalhes, "
                            f"ou finalize a resposta com base nas informações já disponíveis."
                        )
                        continue

                # 🚫 Bloqueia releitura do mesmo intervalo com file_reader
                if tool == "file_reader" and file_path and "start_line" in args and "end_line" in args:
                    chunk_key = f"file_reader_{file_path}_{args['start_line']}_{args['end_line']}"
                    tool_usage_count[chunk_key] = tool_usage_count.get(chunk_key, 0) + 1
                    if tool_usage_count[chunk_key] > 1:
                        self._emit("hard_block", {"file": file_path, "reason": "chunk repetido"})
                        if self.verbose:
                            print(f"[DEBUG] Bloqueada releitura do chunk {args['start_line']}-{args['end_line']} de {file_path}.")
                        prompt = (
                            f"O trecho {args['start_line']}-{args['end_line']} de '{file_path}' já foi lido. "
                            f"Você já possui o conteúdo completo do arquivo. "
                            f"Produza a resposta final com as sugestões de melhoria AGORA, sem usar mais ferramentas."
                        )
                        continue

                # 🚫 Bloqueia qualquer leitura adicional de arquivo já totalmente lido
                if tool == "file_reader" and file_path:
                    fully_read_key = f"fully_read_{file_path}"
                    if tool_usage_count.get(fully_read_key, 0) > 0:
                        self._emit("hard_block", {"file": file_path, "reason": "arquivo já totalmente lido"})
                        if self.verbose:
                            print(f"[DEBUG] Bloqueada leitura de '{file_path}' – arquivo já totalmente lido.")
                        prompt = (
                            f"O arquivo '{file_path}' já foi lido completamente. "
                            f"Você já possui todo o conteúdo necessário. "
                            f"Produza a resposta final com as sugestões AGORA, sem usar mais ferramentas."
                        )
                        continue

                if tool == self.agent_state.last_tool and args == self.agent_state.last_args:
                    loop_count += 1
                    if loop_count >= self.max_loop_repetitions:
                        self._emit("loop_detected", {"tool": tool, "count": loop_count})
                        print("⚠️ Loop de ferramenta detectado. Encerrando.")
                        ans = f"Loop de ferramenta ({tool}). Tarefa interrompida."
                        self.agent_state.conversation_history.append({"user": objective, "agent": ans})
                        return ans
                else:
                    loop_count = 0

                usage_key = json.dumps((tool, args), sort_keys=True, default=str)
                tool_usage_count[usage_key] = tool_usage_count.get(usage_key, 0) + 1
                if tool_usage_count[usage_key] > self.max_loop_repetitions:
                    self._emit("loop_detected", {"tool": tool, "key": usage_key})
                    print("⚠️ Ferramenta já usada muitas vezes com os mesmos argumentos. Encerrando.")
                    ans = f"Repetição excessiva da ferramenta {tool}."
                    self.agent_state.conversation_history.append({"user": objective, "agent": ans})
                    return ans

                self._emit("tool_start", {"tool": tool, "args": args})
                result = self._run_tool(tool, args)
                self._emit("tool_end", {"tool": tool, "ok": result.get("ok")})
                self._maybe_summarize_and_store(tool, args, result)

                if tool == "file_reader" and result.get("ok") and "total_lines" in result:
                    total_lines = result["total_lines"]
                    end_line = args.get("end_line", total_lines)
                    if end_line == total_lines:
                        fully_read_key = f"fully_read_{file_path}"
                        tool_usage_count[fully_read_key] = 1
                        if self.verbose:
                            print(f"[DEBUG] Arquivo '{file_path}' completamente lido ({total_lines} linhas). Bloqueando futuras leituras.")

                result_str = stringify(result)
                MAX_RESULT_CHARS = 4000
                if len(result_str) > MAX_RESULT_CHARS:
                    file_path = args.get("target") or args.get("file_path")
                    if file_path and file_path in self.agent_state.memory.state.get("analyzed_files", {}):
                        result_str = f"[Resumo armazenado] {self.agent_state.memory.state['analyzed_files'][file_path]}"
                    else:
                        result_str = result_str[:MAX_RESULT_CHARS] + "... (truncado)"

                prompt = (
                    f"OBJETIVO: {objective}\n\n"
                    f"ÚLTIMA FERRAMENTA: {tool}\n"
                    f"ARGUMENTOS: {stringify(args)}\n"
                    f"RESULTADO DA TOOL: {result_str}\n\n"
                    "Decida: usar outra ferramenta ou retornar 'final' apenas se a última tool tiver ok=true e done=true."
                )
                continue

            print(f"❌ Ação desconhecida: {action}")
            break

        ans = "Número máximo de passos atingido."
        self.agent_state.conversation_history.append({"user": objective, "agent": ans})
        return ans

    # ==================================================================
    # Executor de planos controlado (modo principal)
    # ==================================================================
    def run(self, objective: str) -> str:
        original_msg_count = len(self.session.messages)
        tool_usage_count: Dict[str, int] = {}

        try:
            # Reset do estado
            self.agent_state.objective = objective
            self.agent_state.plan = []
            self.agent_state.plan_step = 0
            self.agent_state.last_result = None
            self.agent_state.last_tool = None
            self.agent_state.last_args = None
            self.agent_state.tool_history = []
            self.agent_state.events.clear()
            self._cached_project_context = None

            print(f"\n🤖 Analisando: \"{objective}\"")
            logger.info(f"Iniciando objetivo do agente: {objective}")

            # ----------------------------------------------------------
            # Verificação de tarefa trivial (sem router, sem plano)
            # ----------------------------------------------------------
            if _is_clearly_trivial(objective):
                decision = self._ask_model(objective, step_type="final")
                answer = decision.get("answer", "Olá! Como posso ajudar?")
                self._emit("final", {"answer": answer[:100]})
                self.agent_state.conversation_history.append({"user": objective, "agent": answer})
                return answer

            # ----------------------------------------------------------
            # Roteamento de persona (apenas para tarefas não triviais)
            # ----------------------------------------------------------
            if self.verbose:
                print("🧭 Consultando roteador de persona...", end="", flush=True)
            persona_prompt, allowed_skills = route_objective(objective, self.session)
            if self.verbose:
                print(f" ✓ ({len(allowed_skills)} skills permitidas)")

            self.current_persona_prompt = persona_prompt
            self.active_skills = allowed_skills

            # ----------------------------------------------------------
            # Fase 1: Gerar o plano (com dicas de tamanho de arquivos)
            # ----------------------------------------------------------

            if os.path.exists("analysis_notes.md"):
                try:
                    with open("analysis_notes.md", "w", encoding="utf-8") as f:
                        f.write("")
                except Exception:
                    pass
                    
            file_hints = self._get_file_hints(objective)
            hint_block = ""
            if file_hints:
                hint_block = (
                    "\n\n**Tamanhos de arquivos conhecidos (use para planejar chunks):**\n" +
                    file_hints +
                    "\n"
                )

            tools_desc_compact = self._build_tools_description(compact=True)

            plan_prompt = (
                f"Objetivo: {objective}{hint_block}\n\n"
                f"Ferramentas disponíveis:\n{tools_desc_compact}\n\n"
                "Com base nessas ferramentas, crie um plano sequencial ESTRITAMENTE ORDENADO. "
                "Cada passo deve conter EXATAMENTE UMA chamada de ferramenta. "
                "NÃO agrupe múltiplas ações em um único passo.\n"
                "Formato de cada passo: 'Usar [ferramenta] com [parâmetros] em [alvo]'.\n"
                "Se o arquivo tiver muitas linhas, crie UM PASSO POR CHUNK "
                "(ex.: 'Ler [ARQUIVO] linhas 1-100', depois 'Ler [ARQUIVO] linhas 101-200').\n"
                "NÃO inclua passos para deletar, apagar, limpar ou esvaziar arquivos."
                "Responda APENAS com um JSON contendo o campo 'plan' (lista de strings).\n"
                "Exemplo genérico (NÃO USE ESTES VALORES, use os do objetivo acima): {\"plan\": ["
                "\"Usar code_analyzer com mode='file' e compact=true em [ARQUIVO]\", "
                "\"Usar file_reader para ler [ARQUIVO] linhas 1-100\", "
                "\"Usar file_reader para ler [ARQUIVO] linhas 101-200\""
                "]}"
            )
            plan_decision = self._ask_model(plan_prompt, step_type="plan")

            plan = plan_decision.get("plan")
            if plan and isinstance(plan, list) and len(plan) > 0:
                # --------------------------------------------------
                # Filtro de segurança: remove passos que tentam deletar
                # o arquivo de notas (analysis_notes.md)
                # --------------------------------------------------
                filtered_plan = []
                for step in plan:
                    step_lower = step.lower()
                    # Bloqueia passos de deleção/limpeza
                    if ("deletar" in step_lower or "apagar" in step_lower or 
                        "excluir" in step_lower or "remover" in step_lower or 
                        "limpar" in step_lower) and "analysis_notes" in step_lower:
                        if self.verbose:
                            print(f"[DEBUG] Removido passo de deleção do plano: {step}")
                        continue
                    # Bloqueia file_writer que esvazia o arquivo
                    if ("file_writer" in step_lower and "analysis_notes.md" in step_lower and
                        ("content=''" in step_lower or 'content=""' in step_lower)):
                        if self.verbose:
                            print(f"[DEBUG] Removido passo que esvazia analysis_notes.md: {step}")
                        continue
                    filtered_plan.append(step)

                self.agent_state.plan = filtered_plan
                self.agent_state.plan_step = 0
                self._emit("plan_created", {"steps": len(filtered_plan), "plan": filtered_plan})
                if self.verbose:
                    print(f"[DEBUG] Plano filtrado com {len(filtered_plan)} passos: {filtered_plan}")
            else:
                if self.verbose:
                    print("[DEBUG] Plano não gerado, usando modo reativo.")
                return self._run_reactive(objective, tool_usage_count, original_msg_count)

            # ----------------------------------------------------------
            # Fase 2: Executar cada passo do plano
            # ----------------------------------------------------------
            for i, step in enumerate(self.agent_state.plan):
                self.agent_state.plan_step = i + 1

                # Monta prompt para execução do passo
                progress_lines = []
                for j, s in enumerate(self.agent_state.plan):
                    if j < i:
                        marker = "✓"
                    elif j == i:
                        marker = "→"
                    else:
                        marker = "○"
                    progress_lines.append(f"{marker} Passo {j+1}: {s}")
                progress = "\n".join(progress_lines)

                step_prompt = (
                    f"Plano de execução:\n{progress}\n\n"
                    f"Agora execute o Passo {i+1}: \"{step}\"\n\n"
                    "Responda APENAS com o JSON da ação necessária:\n"
                    "{\"action\":\"tool\",\"tool\":\"<nome>\",\"args\":{...}}\n"
                    "Se este passo já foi concluído ou é trivial, responda:\n"
                    "{\"action\":\"final\",\"answer\":\"<texto>\"}"
                )

                # Se o passo for de escrita, forçar divisão em partes pequenas
                if "file_writer" in step.lower() or "analysis_notes" in step.lower():
                    already_written = any(
                        "file_writer" in h.get("tool", "") and h.get("result", {}).get("ok")
                        for h in self.agent_state.tool_history
                    )
                    if not already_written:
                        write_mode = "action='write' (CRIAR o arquivo)"
                    else:
                        write_mode = "action='append' (ADICIONAR ao arquivo existente)"

                    step_prompt += (
                        f"\n\n**IMPORTANTE:** Use file_writer com {write_mode}. "
                        "O conteúdo DEVE ter NO MÁXIMO 300 caracteres. "
                        "Divida a análise em MÚLTIPLAS chamadas curtas. "
                        "NUNCA tente escrever mais de 300 caracteres de uma vez."
                    )

                # Tenta executar o passo (com retry em caso de erro)
                max_retries = 2
                for attempt in range(max_retries):
                    decision = self._ask_model(step_prompt, step_type="tool_decision")

                    if decision.get("action") == "error":
                        if attempt == max_retries - 1:
                            break
                        continue

                    valid, error_msg = validate_decision(decision)
                    if not valid:
                        if attempt == max_retries - 1:
                            break
                        continue

                    action = decision["action"]

                    if action == "final":
                        answer = decision.get("answer", "")
                        self._emit("final", {"answer": answer[:100]})
                        self.agent_state.conversation_history.append({"user": objective, "agent": answer})
                        return answer

                    if action == "tool":
                        tool = decision["tool"]
                        args = decision.get("args", {})
                        if not isinstance(args, dict):
                            args = {}

                        file_path = args.get("target") or args.get("file_path")

                        # 🔍 Pula chunks impossíveis (start_line > total_lines conhecido)
                        if tool == "file_reader" and "start_line" in args and "end_line" in args:
                            known_total = None
                            if file_path:
                                for h in self.agent_state.tool_history:
                                    if h["tool"] == "file_reader" and h.get("result", {}).get("total_lines"):
                                        h_file = h.get("args", {}).get("file_path") or h.get("args", {}).get("target")
                                        if h_file == file_path:
                                            known_total = h["result"]["total_lines"]
                                            break
                            if known_total and args["start_line"] > known_total:
                                if self.verbose:
                                    print(f"[DEBUG] Pulando passo: start_line ({args['start_line']}) > total_lines ({known_total}) para '{file_path}'.")
                                break  # sai do retry e vai para o próximo passo

                        # 🚫 Bloqueia code_analyzer repetido para o mesmo arquivo
                        if tool == "code_analyzer" and file_path:
                            code_analyzer_key = f"code_analyzer_{file_path}"
                            tool_usage_count[code_analyzer_key] = tool_usage_count.get(code_analyzer_key, 0) + 1
                            if tool_usage_count[code_analyzer_key] > 1:
                                self._emit("hard_block", {"file": file_path, "reason": "code_analyzer repetido"})
                                if self.verbose:
                                    print(f"[DEBUG] Bloqueada reanálise estrutural de {file_path}. Use file_reader para detalhes.")
                                step_prompt += "\n\nO code_analyzer já foi usado para este arquivo. Use file_reader ou finalize."
                                continue

                        # 🚫 Bloqueia releitura do mesmo intervalo com file_reader
                        if tool == "file_reader" and file_path and "start_line" in args and "end_line" in args:
                            chunk_key = f"file_reader_{file_path}_{args['start_line']}_{args['end_line']}"
                            tool_usage_count[chunk_key] = tool_usage_count.get(chunk_key, 0) + 1
                            if tool_usage_count[chunk_key] > 1:
                                self._emit("hard_block", {"file": file_path, "reason": "chunk repetido"})
                                if self.verbose:
                                    print(f"[DEBUG] Bloqueada releitura do chunk {args['start_line']}-{args['end_line']} de {file_path}.")
                                step_prompt += "\n\nEsse trecho já foi lido. Use outro intervalo ou finalize."
                                continue

                        # 🚫 Bloqueia qualquer leitura adicional de arquivo já totalmente lido
                        if tool == "file_reader" and file_path:
                            fully_read_key = f"fully_read_{file_path}"
                            if tool_usage_count.get(fully_read_key, 0) > 0:
                                self._emit("hard_block", {"file": file_path, "reason": "arquivo já totalmente lido"})
                                if self.verbose:
                                    print(f"[DEBUG] Bloqueada leitura de '{file_path}' – arquivo já totalmente lido.")
                                step_prompt += "\n\nO arquivo já foi lido completamente. Não leia novamente. Finalize a resposta."
                                continue

                        self._emit("tool_start", {"tool": tool, "args": args})
                        result = self._run_tool(tool, args)
                        self._emit("tool_end", {"tool": tool, "ok": result.get("ok")})
                        self._maybe_summarize_and_store(tool, args, result)

                        if tool == "file_reader" and result.get("ok") and "total_lines" in result:
                            total_lines = result["total_lines"]
                            end_line = args.get("end_line", total_lines)
                            if end_line == total_lines:
                                fully_read_key = f"fully_read_{file_path}"
                                tool_usage_count[fully_read_key] = 1
                                if self.verbose:
                                    print(f"[DEBUG] Arquivo '{file_path}' completamente lido ({total_lines} linhas).")

                        if result.get("ok"):
                            break  # sucesso, vai para próximo passo
                        else:
                            if attempt == max_retries - 1:
                                self._emit("error", {"step": i+1, "error": result.get("error")})

            # ----------------------------------------------------------
            # Fase 3: Resposta final (com retry e fallback)
            # ----------------------------------------------------------
            notes_content = ""
            if os.path.exists("analysis_notes.md"):
                try:
                    with open("analysis_notes.md", "r", encoding="utf-8") as f:
                        notes_content = f.read(4000)
                except Exception:
                    pass

            if notes_content:
                final_base = (
                    f"Objetivo concluído: {objective}\n\n"
                    f"Conteúdo das notas de análise:\n```\n{notes_content}\n```\n\n"
                )
            else:
                final_base = (
                    f"Objetivo concluído: {objective}\n\n"
                    "Com base em TODAS as ferramentas executadas e seus resultados "
                    "(que estão no histórico desta conversa),\n"
                )

            final_prompt = final_base + (
                "Gere AGORA a resposta final em português. "
                "Responda SOMENTE com um JSON: {\"action\":\"final\",\"answer\":\"...\"}. "
                "NÃO use ferramentas. NÃO inclua plano."
            )

            max_final_attempts = 2
            answer = None
            for attempt in range(max_final_attempts):
                final_decision = self._ask_model(final_prompt, step_type="final")
                if final_decision.get("action") == "final" and "answer" in final_decision:
                    answer = final_decision["answer"]
                    break
                # Se veio tool call ou sem answer, reforça o prompt
                final_prompt = (
                    "Sua última resposta NÃO foi um JSON com 'action':'final'. "
                    "Agora, SEM USAR FERRAMENTAS, produza a resposta final em português "
                    "no campo 'answer'. Exemplo: {\"action\":\"final\",\"answer\":\"Minha resposta\"}"
                )

            if not answer:
                # Fallback: tenta extrair qualquer texto útil da última resposta
                raw = final_decision.get("raw_response", "") if 'final_decision' in locals() else ""
                if raw:
                    # Tenta obter um JSON com answer no final
                    json_end = extract_json_from_end(raw) if 'extract_json_from_end' in globals() else None
                    if json_end and json_end.get("answer"):
                        answer = json_end["answer"]
                if not answer:
                    answer = "Não foi possível gerar uma resposta final automática. Por favor, verifique os resultados manualmente."

            # --- Pós-validação anti-alucinação: verifica se a resposta menciona arquivos não lidos ---
            mentioned_files = set(re.findall(r'\b[\w\-/]+\.(?:py|json|yaml|yml|md|txt|toml|cfg)\b', answer))
            read_files = set()
            for h in self.agent_state.tool_history:
                fp = h.get("args", {}).get("file_path") or h.get("args", {}).get("target", "")
                if fp:
                    read_files.add(fp)
            unread = mentioned_files - read_files
            if unread:
                answer += "\n\n[⚠️ Aviso: esta análise menciona arquivos que não foram lidos durante a execução: "
                answer += ", ".join(sorted(unread))
                answer += ". As sugestões relacionadas a esses arquivos podem ser imprecisas.]"

            self.agent_state.conversation_history.append({"user": objective, "agent": answer})
            return answer

        finally:
            while len(self.session.messages) > original_msg_count:
                self.session.messages.pop()
            if len(self.agent_state.conversation_history) > self.agent_state.max_history_turns:
                self.agent_state.conversation_history = self.agent_state.conversation_history[-self.agent_state.max_history_turns:]
            self.save_memory_to_file("agent_memory.json")
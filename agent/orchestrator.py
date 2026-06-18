import json
import os
import sys
from typing import Any, Dict, Optional, Tuple, List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from session import ChatSession
from logger import logger
from agent.prompts import AGENT_SYSTEM_PROMPT, ERROR_PATTERNS
from agent.parsers import extract_json, stringify, validate_decision, normalize_tool_result
from agent.state import AgentState

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

        # Estado unificado do agente
        self.agent_state = AgentState()

        if skills:
            for s in skills:
                self.register_skill(s)

    # ---- Skills ----
    def register_skill(self, skill: Any) -> None:
        self.skills[skill.name] = skill

    def unregister_skill(self, name: str) -> None:
        self.skills.pop(name, None)

    def _build_tools_description(self) -> str:
        out = []
        for s in self.skills.values():
            if not self.active_skills or s.name in self.active_skills:
                out.append(f"- {s.name}: {s.description}\nArgs: {json.dumps(s.get_schema(), indent=2, ensure_ascii=False)}")
        return "\n".join(out)

    # ---- Delegação para a memória (mantém compatibilidade com comandos atuais) ----
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

    # ---- Emissão de eventos ----
    def _emit(self, event_type: str, data: Dict[str, Any] = None) -> None:
        """Registra um evento de telemetria."""
        event = {
            "type": event_type,
            "step": self.agent_state.objective is not None,  # passo atual será obtido do loop
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

    # ---- Helpers (resumo e verificação) ----
    def _maybe_summarize_and_store(self, tool_name: str, args: Dict[str, Any], result: Dict[str, Any]) -> None:
        if tool_name in ("code_analyzer", "file_reader") and result.get("ok"):
            file_path = args.get("target") or args.get("file_path")
            if file_path and "data" in result:
                content = result.get("data")
                if isinstance(content, dict):
                    content = stringify(content)
                if content and len(content) > 300:
                    summary = self._summarize_text(content, context=f"Arquivo: {file_path}")
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

    def _build_project_context(self) -> str:
        try:
            import subprocess
            result = subprocess.run(
                ["git", "ls-files", "--others", "--cached", "--exclude-standard"],
                capture_output=True, text=True, timeout=5, cwd=os.getcwd()
            )
            if result.returncode == 0 and result.stdout.strip():
                files = result.stdout.strip().splitlines()[:50]
                file_list = "\n".join(f"  {f}" for f in files)
                return f"\n\n--- CONTEXTO DO PROJETO ---\nArquivos rastreados pelo Git ({len(files)} arquivos):\n{file_list}\n"
        except Exception:
            pass
        try:
            root = os.getcwd()
            entries = []
            for item in sorted(os.listdir(root)):
                if item.startswith(".") or item == "__pycache__":
                    continue
                full = os.path.join(root, item)
                tag = "/" if os.path.isdir(full) else ""
                entries.append(f"  {item}{tag}")
            return f"\n\n--- CONTEXTO DO PROJETO ---\nEstrutura raiz:\n" + "\n".join(entries[:40]) + "\n"
        except Exception:
            return ""

    # ---- Chamada ao modelo ----
    def _ask_model(self, prompt: str) -> Dict[str, Any]:
        analyzed_context = ""
        if self.agent_state.memory.state.get("analyzed_files"):
            analyzed_context = "\n\n--- ARQUIVOS JÁ ANALISADOS ---\n"
            for file, summary in self.agent_state.memory.state["analyzed_files"].items():
                analyzed_context += f"- {file}: {summary}\n"
            analyzed_context += "NÃO reanalise arquivos já listados aqui, a menos que o usuário peça explicitamente.\n"

        original = self.session.messages[0]["content"]

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

        project_context = self._build_project_context()

        self.session.messages[0]["content"] = (
            persona_prefix + "\n\n"
            + AGENT_SYSTEM_PROMPT.format(tools_description=self._build_tools_description())
            + datetime_context
            + project_context
            + history_context
            + memory_context
        )

        self.session.add_user_message(prompt)
        payload = self.session.build_payload()
        payload["max_tokens"] = self.session.config.get("agent_max_tokens", 8192)
        payload["stream"] = False

        if self.verbose:
            print("⏳ Consultando o modelo...", end="", flush=True)
            
        try:
            response = self.session.send_non_streaming_request(payload)
        except Exception as e:
            logger.error(f"Erro na requisição ao modelo: {e}")
            response = f"Erro na requisição: {e}"
            
        if self.verbose:
            print(" ✓")

        self.session.messages[0]["content"] = original
        self.session.remove_last_user_message()

        if self.verbose:
            print(f"[DEBUG] Resposta bruta: {str(response)[:300]}")

        decision = extract_json(response)
        if decision is not None:
            return decision
        return {"action": "error", "message": "Falha ao extrair JSON da resposta.", "raw_response": str(response)}

    # ---- Execução de ferramenta ----
    def _run_tool(self, tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
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

    # ---- Loop principal ----
    def run(self, objective: str) -> str:
        original_msg_count = len(self.session.messages)
        tool_usage_count: Dict[str, int] = {}

        try:
            from agent.router import route_objective
            
            self.agent_state.objective = objective
            self.agent_state.plan = []
            self.agent_state.plan_step = 0
            self.agent_state.last_result = None
            self.agent_state.last_tool = None
            self.agent_state.last_args = None
            self.agent_state.tool_history = []
            self.agent_state.events.clear()

            early_final_count = 0
            loop_count = 0
            total_actions = 0

            print(f"\n🤖 Analisando: \"{objective}\"")
            logger.info(f"Iniciando objetivo do agente: {objective}")
            
            if self.verbose:
                print("🧭 Consultando roteador de persona...", end="", flush=True)
            persona_prompt, allowed_skills = route_objective(objective, self.session)
            if self.verbose:
                print(f" ✓ ({len(allowed_skills)} skills permitidas)")
                
            self.current_persona_prompt = persona_prompt
            self.active_skills = allowed_skills

            plan_context = ""
            if self.agent_state.plan:
                plan_lines = []
                for i, step in enumerate(self.agent_state.plan):
                    marker = "✓" if i < self.agent_state.plan_step else "→" if i == self.agent_state.plan_step else "○"
                    plan_lines.append(f"{marker} Passo {i+1}: {step}")
                plan_context = "\n\n--- PLANO DE EXECUÇÃO ---\n" + "\n".join(plan_lines) + "\nSiga o plano. Marque cada passo como concluído após executá-lo."
                
            prompt = objective + plan_context
            error_streak = 0
            while self.agent_state.plan_step < self.max_steps:
                self.agent_state.plan_step += 1
                total_actions += 1

                if total_actions > self.max_total_actions:
                    print("⚠️ Limite de ações atingido. Encerrando.")
                    last = self.agent_state.last_result or {}
                    ans = f"Tarefa não resolvida no limite de ações. Último resultado: {stringify(last)}"
                    self.agent_state.conversation_history.append({"user": objective, "agent": ans})
                    return ans

                decision = self._ask_model(prompt)

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

                # Extrai e armazena o plano (se existir)
                plan = decision.get("plan")
                if plan and isinstance(plan, list):
                    self.agent_state.plan = plan
                    self.agent_state.plan_step = 0
                    self._emit("plan_created", {"steps": len(plan), "plan": plan})
                    if self.verbose:
                        print(f"[DEBUG] Plano recebido com {len(plan)} passos: {plan}")

                if action == "final":
                    if self._is_task_solved():
                        answer = decision.get("answer", "")
                        self._emit("final", {"answer": answer[:100]})
                        if self.verbose:
                            print(f"[DEBUG] Final aceito: {answer[:100]}...")
                        logger.info(f"Tarefa resolvida com sucesso: {answer[:50]}...")
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
                    if (tool in ("code_analyzer", "file_reader") and 
                        file_path and 
                        file_path in self.agent_state.memory.state.get("analyzed_files", {})):
                        self._emit("hard_block", {"file": file_path})
                        if self.verbose:
                            print(f"[DEBUG] Bloqueada reanálise de {file_path}. Usando resumo existente.")
                        prompt = (
                            f"O arquivo '{file_path}' já foi analisado e seu resumo está na memória da sessão. "
                            f"Use as informações existentes em SESSION MEMORY. "
                            f"Escolha outra ação ou finalize com uma resposta baseada no que já sabe."
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

                    if self.agent_state.plan and self.agent_state.plan_step < len(self.agent_state.plan):
                        self.agent_state.plan_step += 1

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

        finally:
            while len(self.session.messages) > original_msg_count:
                self.session.messages.pop()
            if len(self.agent_state.conversation_history) > self.agent_state.max_history_turns:
                self.agent_state.conversation_history = self.agent_state.conversation_history[-self.agent_state.max_history_turns:]
            self.save_memory_to_file("agent_memory.json")
import json
import os
import re
import sys
from typing import Any, Dict, Optional, Tuple, List
import datetime
import time
import glob
import hashlib


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from session import ChatSession
from logger import logger
from agent.prompts import AGENT_SYSTEM_PROMPT, ERROR_PATTERNS
from agent.parsers import extract_json, stringify, validate_decision, normalize_tool_result, extract_json_from_end
from agent.state import AgentState
from agent.router import route_objective, _is_clearly_trivial

# Limites de custo por tarefa (valores padrão, podem ser sobrescritos em config.json)
DEFAULT_MAX_TASK_STEPS = 20          # passos do plano
DEFAULT_MAX_TASK_TOKENS = 25000      # tokens estimados de toda a conversa na tarefa
DEFAULT_MAX_TASK_TOOL_CALLS = 40     # chamadas totais a ferramentas

CONTEXT_LIMIT = 8192
CONTEXT_COMPRESSION_THRESHOLD = 0.8  # 80%

AGENT_METRICS_FILE = "agent_metrics.jsonl"

MAX_MEMORY_BACKUPS = 5  # número máximo de arquivos de backup mantidos
MEMORY_BACKUP_DIR = "memory_backups"

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
        self._restore_points: List[Dict[str, str]] = []      # backup -> original
        self._task_failed = False

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
        """Salva a memória em disco com backup rotativo em subpasta."""
        self._backup_memory_file(path)
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
                    # Armazena hash do ARQUIVO REAL (não do resultado da ferramenta)
                    try:
                        with open(file_path, 'r', encoding='utf-8') as f:
                            file_hash = hashlib.sha256(f.read().encode('utf-8')).hexdigest()
                        self.agent_state.memory.state.setdefault("file_hashes", {})[file_path] = file_hash
                    except Exception:
                        pass

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

    def _validate_args(self, tool_name: str, args: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
        """
        Valida os argumentos de uma ferramenta contra o schema que ela exporta.
        Retorna (válido, mensagem_de_erro).
        """
        skill = self.skills.get(tool_name)
        if not skill:
            return True, None  # ferramenta desconhecida, deixa executar e falhar depois

        schema = skill.get_schema()
        if not schema or not isinstance(schema, dict):
            return True, None  # sem schema, não valida

        required = schema.get("required", [])
        properties = schema.get("properties", {})
        errors = []

        # 1. Verifica campos obrigatórios
        for field in required:
            if field not in args or args[field] is None:
                errors.append(f"Campo obrigatório ausente: '{field}'")

        # 2. Verifica tipos e valores permitidos
        for field, value in args.items():
            prop = properties.get(field)
            if not prop:
                continue  # campo extra, ignoramos (poderia ser erro, mas é permissivo)

            expected_type = prop.get("type", "string")
            actual_type = type(value).__name__

            # Validação de tipo
            if expected_type == "string" and not isinstance(value, str):
                errors.append(f"'{field}': esperado string, recebido {actual_type}")
            elif expected_type == "number" and not isinstance(value, (int, float)):
                errors.append(f"'{field}': esperado número, recebido {actual_type}")
            elif expected_type == "boolean" and not isinstance(value, bool):
                errors.append(f"'{field}': esperado booleano, recebido {actual_type}")
            elif expected_type == "object" and not isinstance(value, dict):
                errors.append(f"'{field}': esperado objeto, recebido {actual_type}")
            elif expected_type == "array" and not isinstance(value, list):
                errors.append(f"'{field}': esperado array, recebido {actual_type}")

            # Validação de enum (valores permitidos)
            allowed = prop.get("enum")
            if allowed and value not in allowed:
                errors.append(f"'{field}': valor '{value}' não está entre os permitidos: {allowed}")

            # Validação de range numérico
            if expected_type == "number" and isinstance(value, (int, float)):
                minimum = prop.get("minimum")
                maximum = prop.get("maximum")
                if minimum is not None and value < minimum:
                    errors.append(f"'{field}': valor {value} é menor que o mínimo {minimum}")
                if maximum is not None and value > maximum:
                    errors.append(f"'{field}': valor {value} é maior que o máximo {maximum}")

        # 3. Validações específicas por ferramenta
        if tool_name == "file_reader":
            start = args.get("start_line")
            end = args.get("end_line")
            if start is not None and end is not None:
                if start > end:
                    errors.append(f"'start_line' ({start}) não pode ser maior que 'end_line' ({end})")
            file_path = args.get("file_path", "")
            if file_path and not os.path.exists(file_path):
                errors.append(f"Arquivo não encontrado: '{file_path}'")

        if tool_name == "file_writer":
            action = args.get("action", "write")
            if action == "ast_patch":
                if not args.get("target"):
                    errors.append("Campo 'target' obrigatório para ast_patch")
                if not args.get("new_code"):
                    errors.append("Campo 'new_code' obrigatório para ast_patch")

        if errors:
            return False, "; ".join(errors)
        return True, None

    def _is_task_solved(self) -> bool:
        if not self.agent_state.tool_history:
            return True
        r = self.agent_state.last_result
        if not isinstance(r, dict):
            return False
        return r.get("ok") is True and r.get("done") is True

    def _log_metric(self, entry: Dict[str, Any]) -> None:
        """Adiciona uma linha de métrica ao arquivo JSONL."""
        try:
            with open(AGENT_METRICS_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.warning(f"Falha ao registrar métrica: {e}")

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

    # ------------------
    # Comprime contexto caso chegue perto de estourar a janela
    # ------------------
    def _estimate_conversation_tokens(self) -> int:
        """Estima o total de tokens em self.session.messages (heurística 1 token ≈ 4 chars)."""
        total_chars = sum(len(str(m.get("content", ""))) for m in self.session.messages)
        return total_chars // 4

    def _maybe_compress_context(self) -> None:
        """Se a conversa estiver muito grande, comprime o histórico em um resumo."""
        estimated = self._estimate_conversation_tokens()
        threshold = int(CONTEXT_LIMIT * CONTEXT_COMPRESSION_THRESHOLD)

        if estimated <= threshold:
            return

        if self.verbose:
            print(f"⚡ [COMPRESS] Contexto atingiu ~{estimated} tokens (limiar: {threshold}). Comprimindo...")

        # Monta prompt para o modelo resumir o histórico
        compress_prompt = (
            "Resuma a conversa abaixo em um parágrafo denso, mantendo APENAS:\n"
            "- Objetivo original da tarefa\n"
            "- Plano restante (passos já concluídos e pendentes)\n"
            "- Principais descobertas e resultados obtidos\n"
            "- Próximas ações necessárias\n\n"
            "NÃO inclua saudações, repetições ou informações irrelevantes.\n\n"
            "Histórico:\n"
        )
        # Inclui as últimas N mensagens (ex.: as 20 mais recentes) para não estourar
        recent = self.session.messages[-20:]
        for msg in recent:
            compress_prompt += f"[{msg['role']}] {msg['content']}\n"

        # Salva o system prompt original
        original_system = self.session.messages[0]["content"] if self.session.messages else ""

        # Cria uma sessão temporária isolada
        temp_session = ChatSession("", self.session.config)  # system vazio
        temp_session.set_system_prompt("Resuma o histórico da conversa de forma concisa e técnica.")
        temp_session.add_user_message(compress_prompt)

        temp_payload = temp_session.build_payload()
        temp_payload["max_tokens"] = 1024
        temp_payload["stream"] = False
        try:
            temp_response = self.session.send_non_streaming_request(temp_payload)
        except Exception as e:
            logger.warning(f"Falha ao comprimir contexto: {e}")
            return

        if isinstance(temp_response, str) and temp_response.strip():
            summary = temp_response.strip()
            # Limpa o histórico da sessão principal, mantendo o system prompt
            self.session.messages = [{"role": "system", "content": original_system}]
            # Insere o resumo como uma mensagem de sistema adicional
            self.session.add_message("system", f"[RESUMO DO CONTEXTO]: {summary}")
            if self.verbose:
                print(f"✅ [COMPRESS] Contexto comprimido para ~{len(summary)//4} tokens.")
        else:
            logger.warning("Resposta vazia ao comprimir contexto.")

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

    def _check_prompt_size(self, context_limit: int = 8192) -> None:
        """
        Estima o tamanho do system prompt atual e emite aviso se estiver
        acima de 80% do limite de contexto.
        """
        system_content = self.session.messages[0]["content"]
        estimated_tokens = len(system_content) // 4  # heurística comum
        threshold = int(context_limit * 0.8)
        pct = estimated_tokens / context_limit * 100

        if self.verbose:
            print(f"📏 [AUDITORIA] Prefixo estimado: ~{estimated_tokens} tokens ({pct:.1f}% do limite de {context_limit})")
        
        if estimated_tokens > threshold:
            logger.warning(f"Prefixo grande: ~{estimated_tokens} tokens ({pct:.1f}%)")
            if self.verbose:
                print(f"⚠️  Atenção: prefixo acima de 80%! Considere limpar memória ou reduzir histórico.")

    def _count_tokens_precise(self, text: str) -> Optional[int]:
        """
        Usa o endpoint /tokenize do llama-server para contar os tokens de um texto.
        Retorna None se a chamada falhar.
        """
        try:
            import requests
            api_url = self.session.config.get("api_url", "http://127.0.0.1:8080/v1/chat/completions")
            # Extrai a base da URL (remove /v1/chat/completions)
            base_url = api_url.rsplit("/v1/", 1)[0]
            tokenize_url = f"{base_url}/tokenize"
            resp = requests.post(tokenize_url, json={"content": text}, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                tokens = data.get("tokens", [])
                return len(tokens)
        except Exception as e:
            logger.warning(f"Não foi possível usar /tokenize: {e}")
        return None

    #-----------------Evita ficar reenviando system prompts pro modelo ----------------------
    def _build_base_system_prompt(self) -> str:
        """Monta a parte fixa do system prompt (persona + tools + datetime + projeto)."""
        persona_prefix = getattr(self, "current_persona_prompt", "")
        from datetime import datetime
        now_str = datetime.now().strftime("%A, %d de %B de %Y %H:%M")
        datetime_context = f"\n\n[SISTEMA] Data e hora atual: {now_str}. Use esta informação para responder perguntas sobre datas."
        project_context = self._get_project_context()
        # Usa descrição completa das ferramentas, pois é o system prompt permanente da tarefa
        tools_desc = self._build_tools_description(compact=False)
        return (
            persona_prefix + "\n\n"
            + AGENT_SYSTEM_PROMPT.format(tools_description=tools_desc)
            + datetime_context
            + project_context
        )

    # ------------------------------------------------------------------
    # Comunicação com o modelo (com budget variável por step_type)
    # ------------------------------------------------------------------
    def _ask_model(self, prompt: str, step_type: str = "tool_decision") -> Dict[str, Any]:
        # Salva estado original da sessão
        original_messages = [m.copy() for m in self.session.messages]
        original_system_content = self.session.messages[0]["content"]
        # Auditoria de prefixo (estimativa rápida + exata opcional)
        if self.verbose:
            self._check_prompt_size()
            exact = self._count_tokens_precise(self.session.messages[0]["content"])
            if exact is not None:
                print(f"📏 [AUDITORIA] Tokens exatos: {exact}")

        try:
            # --- Montagem do contexto ---
            # --- Partes dinâmicas: histórico e memória ---
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

            # Usa a base fixa cacheada (persona + tools + datetime + projeto)
            base_prompt = getattr(self, '_cached_base_prompt', None)
            if base_prompt is None:
                # Fallback (não deve ocorrer em uso normal)
                base_prompt = self._build_base_system_prompt()

            self.session.messages[0]["content"] = (
                base_prompt + history_context + memory_context
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

            start_time = time.time()
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
            # Coleta métricas
            duration_ms = int((time.time() - start_time) * 1000)
            prompt_tokens = None
            completion_tokens = None
            if isinstance(response, dict):
                usage = response.get("usage") or {}
                prompt_tokens = usage.get("prompt_tokens")
                completion_tokens = usage.get("completion_tokens")

            metric = {
                "timestamp": datetime.datetime.now().isoformat(),
                "step_type": step_type,
                "tool": decision.get("tool") if isinstance(decision, dict) else None,
                "budget": budget,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "duration_ms": duration_ms,
                "success": decision is not None and isinstance(decision, dict) and "action" in decision
            }
            self._log_metric(metric)

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

    def _backup_memory_file(self, path: str, max_backups: int = MAX_MEMORY_BACKUPS) -> None:
        """
        Cria uma cópia de segurança do arquivo de memória dentro da pasta MEMORY_BACKUP_DIR.
        Mantém apenas os últimos max_backups arquivos.
        """
        if not os.path.exists(path):
            return

        try:
            # Garante que a pasta de backups existe
            os.makedirs(MEMORY_BACKUP_DIR, exist_ok=True)

            # Gera nome com timestamp
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_name = os.path.basename(path) + f".{timestamp}.bak"
            backup_path = os.path.join(MEMORY_BACKUP_DIR, backup_name)

            import shutil
            shutil.copy2(path, backup_path)

            # Remove backups excedentes (apenas os arquivos .bak da pasta)
            all_backups = sorted(
                f for f in os.listdir(MEMORY_BACKUP_DIR)
                if f.startswith(os.path.basename(path)) and f.endswith(".bak")
            )
            while len(all_backups) > max_backups:
                oldest = all_backups.pop(0)
                os.remove(os.path.join(MEMORY_BACKUP_DIR, oldest))
                if self.verbose:
                    print(f"[DEBUG] Backup antigo removido: {MEMORY_BACKUP_DIR}/{oldest}")
        except Exception as e:
            logger.warning(f"Não foi possível criar backup da memória: {e}")

    def _create_restore_point(self) -> None:
        """
        Cria backups de todos os arquivos que o plano pretende modificar.
        """
        if not self.agent_state.plan:
            return

        import shutil
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        restore_dir = os.path.join(MEMORY_BACKUP_DIR, "restore", timestamp)
        os.makedirs(restore_dir, exist_ok=True)

        for step in self.agent_state.plan:
            tool = step.get("tool", "") if isinstance(step, dict) else ""
            args = step.get("args", {}) if isinstance(step, dict) else {}
            if tool in ("file_writer", "shell", "python_executor"):
                file_path = args.get("file_path") or args.get("target") or ""
                if file_path and os.path.exists(file_path):
                    backup_path = os.path.join(restore_dir, file_path.replace(os.sep, "_"))
                    try:
                        shutil.copy2(file_path, backup_path)
                        self._restore_points.append({"original": file_path, "backup": backup_path})
                        if self.verbose:
                            print(f"[DEBUG] Checkpoint salvo para '{file_path}'")
                    except Exception as e:
                        logger.warning(f"Falha ao criar checkpoint para '{file_path}': {e}")

    def _rollback(self) -> None:
        """
        Restaura todos os arquivos a partir dos backups, na ordem inversa.
        """
        if not self._restore_points:
            return

        import shutil
        if self.verbose:
            print("⏪ [ROLLBACK] Restaurando arquivos ao estado original...")

        for entry in reversed(self._restore_points):
            try:
                shutil.copy2(entry["backup"], entry["original"])
                os.remove(entry["backup"])
                if self.verbose:
                    print(f"   ✅ Restaurado: {entry['original']}")
            except Exception as e:
                logger.error(f"Falha ao restaurar '{entry['original']}': {e}")

        self._restore_points.clear()

    def _show_diff(self, file_path: str, new_content: str) -> None:
        """
        Exibe a diferença entre o arquivo original e o novo conteúdo usando difflib.
        """
        import difflib
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                original = f.read()
        except Exception:
            original = ""

        diff = difflib.unified_diff(
            original.splitlines(keepends=True),
            new_content.splitlines(keepends=True),
            fromfile=file_path,
            tofile=f"{file_path} (proposto)",
        )
        diff_text = ''.join(diff)
        if diff_text.strip():
            print(f"\n📝 [DIFF] Mudanças propostas para '{file_path}':")
            print(diff_text)
        else:
            print(f"📝 [DIFF] Nenhuma mudança em '{file_path}'.")

    def _lint_check(self, file_path: str) -> Optional[str]:
        """
        Verifica a sintaxe e boas práticas de um arquivo Python.
        Retorna mensagem de erro se houver problemas, ou None se estiver limpo.
        """
        if not file_path.endswith(".py"):
            return None

        errors = []

        # 1. Verificação de sintaxe (py_compile)
        import py_compile
        try:
            py_compile.compile(file_path, doraise=True)
        except py_compile.PyCompileError as e:
            errors.append(f"Sintaxe: {str(e)}")

        # 2. Verificação de estilo com flake8 (opcional, se instalado)
        try:
            import subprocess
            result = subprocess.run(
                ["flake8", "--max-line-length=120", file_path],
                capture_output=True, text=True, timeout=10
            )
            if result.stdout.strip():
                errors.append(f"Estilo: {result.stdout.strip()}")
        except Exception:
            pass  # flake8 não instalado ou falhou, ignora

        if errors:
            return "\n".join(errors)
        return None

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
            allowed = ", ".join(sorted(self.active_skills)) if self.active_skills else "todas disponíveis"
            result = {"ok": False, "done": False, "data": None,
                    "error": f"Tool '{tool_name}' não está permitida para esta persona. Ferramentas disponíveis: {allowed}",
                    "message": None}
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

    #---------- Fallback -----------
    def _handle_step_failure(self, step_index: int, reason: str,
                             tool: str = "", args: dict = None) -> str:
        """
        Trata falhas na execução de um passo da Fase 2.
        Sanitiza o erro e registra de forma enxuta.
        Retorna "continue", "abort" ou "replan".
        """
        sanitized = self._sanitize_error(reason)
        self._emit("error", {"step": step_index, "error": sanitized})
        logger.warning(f"Passo {step_index} falhou ({tool}): {sanitized}")
        return "continue"

    def _sanitize_error(self, error_message: str) -> str:
        """
        Extrai apenas o tipo do erro, a mensagem essencial e a linha relevante
        de um stack trace ou mensagem de erro, economizando tokens.
        """
        if not error_message:
            return ""

        # Remove quebras de linha duplicadas e espaços excessivos
        cleaned = re.sub(r'\n{3,}', '\n\n', error_message.strip())

        # Tenta extrair a última linha relevante de um traceback Python
        lines = cleaned.split('\n')
        error_type = ""
        error_msg = ""
        relevant_line = ""

        # Procura por padrões de traceback
        for i, line in enumerate(lines):
            # Detecta a linha do erro (ex: "TypeError: ...")
            if re.match(r'^[A-Za-z_]\w*Error:', line):
                error_type = line.split(':')[0].strip()
                error_msg = line
                # Tenta pegar a linha seguinte como contexto
                if i + 1 < len(lines) and lines[i+1].strip():
                    relevant_line = lines[i+1].strip()[:200]
                break

        # Se não encontrou padrão de traceback, retorna versão curta
        if not error_type:
            # Pega apenas as primeiras e últimas linhas
            if len(lines) > 10:
                cleaned = '\n'.join(lines[:3] + ['...'] + lines[-3:])
            return cleaned[:600]

        # Monta versão sanitizada
        sanitized = f"{error_msg}"
        if relevant_line:
            sanitized += f"\n  → {relevant_line}"

        # Adiciona dica de linha se disponível (ex: "line 42")
        line_match = re.search(r'line (\d+)', error_msg)
        if line_match:
            sanitized += f" (linha {line_match.group(1)})"

        return sanitized[:500]

    def _purge_stale_context(self) -> None:
        """
        Remove tentativas antigas da sessão, mantendo apenas:
        - O system prompt original
        - O resumo do contexto (se existir)
        - A última mensagem do usuário
        - O último erro sanitizado
        """
        if len(self.session.messages) <= 2:
            return

        # Preserva o system prompt (índice 0)
        preserved = [self.session.messages[0]]

        # Mantém mensagens de sistema adicionais (ex.: resumo de compressão)
        for msg in self.session.messages[1:]:
            if msg["role"] == "system":
                preserved.append(msg)

        # Mantém a última mensagem do usuário
        last_user_msg = None
        for msg in reversed(self.session.messages):
            if msg["role"] == "user":
                last_user_msg = msg
                break
        if last_user_msg:
            preserved.append(last_user_msg)

        # Substitui o histórico
        self.session.messages = preserved

        if self.verbose:
            print(f"🧹 [PURGE] Contexto limpo: {len(preserved)} mensagens mantidas.")


    def _generate_tests(self, code: str, file_path: str) -> Optional[str]:
        """
        Gera testes unitários para o código fornecido.
        Retorna o código de teste pronto para execução.
        """
        prompt = (
            f"Gere testes unitários em Python para o seguinte código do arquivo '{file_path}':\n\n"
            f"```python\n{code[:4000]}\n```\n\n"
            "Regras:\n"
            "- Use apenas bibliotecas padrão (unittest ou pytest).\n"
            "- Cubra os casos principais e casos de borda.\n"
            "- NÃO inclua mocks de arquivos ou rede.\n"
            "- NÃO use bibliotecas externas.\n"
            "- Retorne APENAS o código Python dos testes, pronto para ser executado."
        )
        decision = self._ask_model(prompt, step_type="tool_decision")
        if isinstance(decision, dict):
            content = decision.get("content") or decision.get("answer") or decision.get("code") or ""
            return content.strip() if content.strip() else None
        if isinstance(decision, str) and decision.strip():
            return decision.strip()
        return None

    def _correct_code(self, original_code: str, file_path: str, test_code: str, error_msg: str) -> Optional[str]:
        """
        Corrige o código original com base no erro de teste.
        Retorna o código corrigido.
        """
        prompt = (
            f"O seguinte código Python do arquivo '{file_path}' falhou nos testes:\n\n"
            f"```python\n{original_code[:4000]}\n```\n\n"
            f"Testes executados:\n```python\n{test_code[:2000]}\n```\n\n"
            f"Erro reportado:\n{self._sanitize_error(error_msg)}\n\n"
            "Corrija APENAS o código original para que os testes passem. "
            "Retorne APENAS o código corrigido completo (incluindo imports)."
        )
        decision = self._ask_model(prompt, step_type="tool_decision")
        if isinstance(decision, dict):
            content = decision.get("content") or decision.get("answer") or decision.get("code") or ""
            return content.strip() if content.strip() else None
        if isinstance(decision, str) and decision.strip():
            return decision.strip()
        return None

    def _test_and_correct(self, file_path: str, objective: str) -> bool:
        """
        Ciclo teste-correção automático.
        Retorna True se os testes passaram (ou não foram necessários),
        False se falhou após todas as tentativas.
        """
        if not file_path.endswith(".py"):
            return True  # só testa arquivos Python

        # Lê o código atual
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                code = f.read()
        except Exception:
            return True  # não consegue ler, não testa

        # Só testa se parece código (contém funções ou classes)
        if "def " not in code and "class " not in code:
            return True

        max_attempts = 3
        current_code = code
        test_code = None

        for attempt in range(max_attempts):
            if self.verbose:
                print(f"🧪 [TEST] Tentativa {attempt + 1}/{max_attempts} para '{file_path}'")

            # Gera ou regenera testes
            test_code = self._generate_tests(current_code, file_path)
            if not test_code:
                if self.verbose:
                    print("   ⚠️ Não foi possível gerar testes, pulando.")
                return True

            # Cria um arquivo temporário com o código + testes
            import tempfile
            import subprocess

            test_file = None
            try:
                # Salva código + testes num arquivo temporário
                combined = f"{current_code}\n\n# --- TESTES ---\n{test_code}"
                with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False, encoding='utf-8') as tmp:
                    tmp.write(combined)
                    test_file = tmp.name

                # Executa o arquivo temporário com timeout
                result = subprocess.run(
                    ["python", test_file],
                    capture_output=True, text=True, timeout=15,
                    cwd=os.path.dirname(os.path.abspath(file_path)) or "."
                )
                output = result.stdout + result.stderr

                if result.returncode == 0 and "FAILED" not in output and "Error" not in output:
                    if self.verbose:
                        print(f"   ✅ Testes passaram na tentativa {attempt + 1}!")
                    # Escreve o código corrigido de volta (se houve correção)
                    if attempt > 0:
                        try:
                            with open(file_path, 'w', encoding='utf-8') as f:
                                f.write(current_code)
                        except Exception:
                            pass
                    return True

                # Testes falharam
                if attempt < max_attempts - 1:
                    corrected = self._correct_code(current_code, file_path, test_code, output)
                    if corrected:
                        current_code = corrected
                        if self.verbose:
                            print(f"   🔄 Código corrigido, retentando...")
                        self._purge_stale_context()
                    else:
                        if self.verbose:
                            print(f"   ⚠️ Não foi possível corrigir o código.")
                        break

            except subprocess.TimeoutExpired:
                if self.verbose:
                    print(f"   ⏱️ Timeout na execução dos testes.")
            except Exception as e:
                if self.verbose:
                    print(f"   ❌ Erro ao executar testes: {e}")
            finally:
                if test_file and os.path.exists(test_file):
                    try:
                        os.remove(test_file)
                    except Exception:
                        pass

        # Todas as tentativas falharam
        self._task_failed = True
        self._emit("error", {"step": self.agent_state.plan_step, "error": "Ciclo teste-correção falhou após todas as tentativas"})
        return False

    def _generate_content(self, tool: str, args: dict, objective: str) -> Optional[str]:
        """
        Gera o conteúdo a ser escrito por file_writer usando o LLM.
        Tenta extrair o conteúdo do texto completo da resposta.
        """
        prompt = (
            f"Objetivo: {objective}\n\n"
            f"Ferramenta: {tool}\n"
            f"Argumentos: {json.dumps({k: v for k, v in args.items() if k != 'content'}, ensure_ascii=False)}\n\n"
            "Retorne APENAS o conteúdo a ser escrito no arquivo, sem formatação extra. "
            "Não use markdown, blocos de código ou explicações."
        )
        decision = self._ask_model(prompt, step_type="tool_decision")

        full_text = ""

        # Coleta todo texto possível da resposta
        if isinstance(decision, dict):
            # Tenta campos estruturados primeiro
            for key in ["content", "answer", "text", "code", "raw_response"]:
                val = decision.get(key, "")
                if val and len(str(val)) > 10:
                    full_text = str(val)
                    break
            # Se não achou, junta tudo que for string
            if not full_text:
                parts = []
                for v in decision.values():
                    if isinstance(v, str) and len(v) > 10:
                        parts.append(v)
                full_text = "\n".join(parts)
        elif isinstance(decision, str) and len(decision) > 10:
            full_text = decision

        if not full_text:
            return None

        # Limpeza agressiva
        cleaned = full_text.strip()
        # Remove blocos de código markdown
        cleaned = re.sub(r'```[a-z]*\s*\n?', '', cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r'```', '', cleaned)
        # Remove cabeçalhos markdown e formatação
        cleaned = re.sub(r'^\*\*.*?\*\*\s*:?\n?', '', cleaned)
        cleaned = re.sub(r'^#{1,6}\s+', '', cleaned, flags=re.MULTILINE)
        # Remove linhas de explicação comuns
        cleaned = re.sub(r'^(Aqui está|Segue|Abaixo| Eis|O conteúdo|Conteúdo:|A poesia).*?\n', '', cleaned, flags=re.IGNORECASE)

        result = cleaned.strip()
        return result if len(result) > 10 else None
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
                self._task_failed = True
                return ans

            # Verifica limites de custo da tarefa
            max_steps = self.session.config.get("max_task_steps", DEFAULT_MAX_TASK_STEPS)
            max_tokens = self.session.config.get("max_task_tokens", DEFAULT_MAX_TASK_TOKENS)
            max_tool_calls = self.session.config.get("max_task_tool_calls", DEFAULT_MAX_TASK_TOOL_CALLS)
            estimated_tokens = self._estimate_conversation_tokens()

            if (self.agent_state.plan_step > max_steps or
                estimated_tokens > max_tokens or
                len(self.agent_state.tool_history) > max_tool_calls):

                self._emit("cost_limit", {
                    "reason": "Limite de custo da tarefa atingido (modo reativo)",
                    "steps": self.agent_state.plan_step,
                    "max_steps": max_steps,
                    "estimated_tokens": estimated_tokens,
                    "max_tokens": max_tokens,
                    "tool_calls": len(self.agent_state.tool_history),
                    "max_tool_calls": max_tool_calls
                })

                summary_parts = []
                if self.agent_state.tool_history:
                    tools_used = set(h["tool"] for h in self.agent_state.tool_history)
                    summary_parts.append(f"Ferramentas usadas: {', '.join(tools_used)}")
                    last = self.agent_state.last_result or {}
                    summary_parts.append(f"Último resultado: {stringify(last)[:500]}")

                answer = (
                    "A tarefa foi interrompida porque atingiu o limite de custo definido. "
                    "Resumo do que foi feito:\n" + "\n".join(summary_parts)
                )
                self.agent_state.conversation_history.append({"user": objective, "agent": answer})
                self._task_failed = True
                return answer


            decision = self._ask_model(prompt, step_type="tool_decision")

            if decision.get("action") == "error":
                error_streak += 1
                if error_streak >= 3:
                    print("⚠️ Muitas falhas consecutivas. Encerrando.")
                    ans = f"Erro persistente: {decision.get('message')}"
                    self.agent_state.conversation_history.append({"user": objective, "agent": ans})
                    self._task_failed = True
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
                    self._task_failed = True
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
                        self._task_failed = True
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
                    self._task_failed = True
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
        self._task_failed = True
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
            self._task_failed = False
            self._restore_points.clear()

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

            # Cache da parte fixa do system prompt (evita reconstrução a cada chamada)
            self._cached_base_prompt = self._build_base_system_prompt()

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
                "Crie um plano sequencial para atingir o objetivo. "
                "Cada passo deve conter exatamente UMA ferramenta.\n"
                "Responda APENAS com um JSON no seguinte formato:\n"
                "{\n"
                '  "plan": [\n'
                '    {"tool": "code_analyzer", "args": {"target": "cli.py", "mode": "file", "compact": true}},\n'
                '    {"tool": "file_reader", "args": {"file_path": "cli.py"}}\n'
                "  ]\n"
                "}\n"
                "Regras:\n"
                "- Use APENAS ferramentas da lista acima.\n"
                "- Cada objeto do plano deve ter os campos 'tool' (string) e 'args' (objeto).\n"
                "- Não inclua comentários, texto extra ou formatação fora do JSON.\n"
                "- Quando o objetivo for analisar um arquivo, inclua SEMPRE um passo para ler o conteúdo com file_reader.\n"
                "- Informe apenas o file_path no file_reader; o sistema divide automaticamente se necessário.\n"
                "- NÃO especifique start_line ou end_line ao usar file_reader, a menos que queira um trecho específico.\n"
                "- NÃO inclua passos para deletar, apagar ou esvaziar arquivos."
                "- Para passos de file_writer, NÃO inclua o conteúdo no campo 'content'. Use 'content' como string vazia (\"\"). O sistema gerará o conteúdo automaticamente."
                "- Para alterar uma parte específica de um arquivo (ex.: uma linha, uma função), prefira usar file_writer com action='patch' (substituição exata de trecho) ou action='ast_patch' (substituição de função/classe por nome).\n"
                "- Só use action='write' quando precisar criar um arquivo novo ou substituir TODO o conteúdo."
            )
            plan_decision = self._ask_model(plan_prompt, step_type="plan")
            plan = plan_decision.get("plan")
            if plan and isinstance(plan, list) and len(plan) > 0:
                # Plano canônico: cada item deve ser {"tool": "...", "args": {...}}
                filtered_plan = []
                for step in plan:
                    if not isinstance(step, dict):
                        continue
                    tool = step.get("tool", "")
                    args = step.get("args", {})
                    # Valida os argumentos contra o schema da ferramenta
                    valid, error_msg = self._validate_args(tool, args)
                    if not valid:
                        if self.verbose:
                            print(f"[DEBUG] Passo removido por schema inválido: {step} → {error_msg}")
                        continue
                    if not isinstance(args, dict):
                        args = {}
                    # Filtro de segurança: não esvaziar analysis_notes.md (qualquer caminho)
                    if tool == "file_writer" and "analysis_notes.md" in str(args.get("file_path", "")):
                        content = str(args.get("content", ""))
                        if content.strip() == "":
                            if self.verbose:
                                print(f"[DEBUG] Removido passo que esvazia analysis_notes.md: {step}")
                            continue
                    filtered_plan.append({"tool": tool, "args": args})

                if not filtered_plan:
                    # Plano ficou vazio após filtro → todos os passos foram bloqueados
                    self._emit("hard_block", {"reason": "plano vazio após filtros"})
                    answer = "Não foi possível executar esta ação. Ela foi bloqueada pelas políticas de segurança do agente."
                    self.agent_state.conversation_history.append({"user": objective, "agent": answer})
                    self._task_failed = True
                    return answer

                self.agent_state.plan = filtered_plan
                self.agent_state.plan_step = 0
                self._emit("plan_created", {"steps": len(filtered_plan), "plan": filtered_plan})
                if self.verbose:
                    print(f"[DEBUG] Plano canônico com {len(filtered_plan)} passos: {filtered_plan}")
            else:
                if self.verbose:
                    print("[DEBUG] Plano não gerado ou inválido, usando modo reativo.")
                return self._run_reactive(objective, tool_usage_count, original_msg_count)
            result = None
            # Cria ponto de restauração se houver operações de escrita
            self._create_restore_point()
            # ----------------------------------------------------------
            # Fase 2: Executar cada passo do plano (modo direto, sem LLM)
            # ----------------------------------------------------------
            for i, step in enumerate(self.agent_state.plan):
                self.agent_state.plan_step = i + 1

                # Verifica limites de custo da tarefa
                max_steps = self.session.config.get("max_task_steps", DEFAULT_MAX_TASK_STEPS)
                max_tokens = self.session.config.get("max_task_tokens", DEFAULT_MAX_TASK_TOKENS)
                max_tool_calls = self.session.config.get("max_task_tool_calls", DEFAULT_MAX_TASK_TOOL_CALLS)

                # Estima tokens da conversa
                estimated_tokens = self._estimate_conversation_tokens()

                if (i + 1 > max_steps or
                    estimated_tokens > max_tokens or
                    len(self.agent_state.tool_history) > max_tool_calls):

                    self._emit("cost_limit", {
                        "reason": "Limite de custo da tarefa atingido",
                        "steps": i + 1,
                        "max_steps": max_steps,
                        "estimated_tokens": estimated_tokens,
                        "max_tokens": max_tokens,
                        "tool_calls": len(self.agent_state.tool_history),
                        "max_tool_calls": max_tool_calls
                    })

                    # Gera resumo do que foi feito
                    summary_parts = []
                    if self.agent_state.tool_history:
                        tools_used = set(h["tool"] for h in self.agent_state.tool_history)
                        summary_parts.append(f"Ferramentas usadas: {', '.join(tools_used)}")
                        summary_parts.append(f"Último resultado: {stringify(self.agent_state.last_result)[:500]}")

                    answer = (
                        "A tarefa foi interrompida porque atingiu o limite de custo definido. "
                        "Resumo do que foi feito:\n" + "\n".join(summary_parts)
                    )
                    self.agent_state.conversation_history.append({"user": objective, "agent": answer})
                    self._task_failed = True
                    return answer

                tool = step["tool"]
                args = step["args"]
                if not isinstance(args, dict):
                    args = {}

                file_path = args.get("target") or args.get("file_path") or ""

                # Validação de schema
                valid, error_msg = self._validate_args(tool, args)
                if not valid:
                    action = self._handle_step_failure(i+1, f"Schema: {error_msg}", tool, args)
                    if action == "continue":
                        self._purge_stale_context()
                        continue
                    else:
                        self._task_failed = True
                        break

                # Verifica permissão da persona
                if tool not in self.skills or (self.active_skills and tool not in self.active_skills):
                    action = self._handle_step_failure(i+1, f"Tool '{tool}' não permitida", tool, args)
                    if action == "continue":
                        self._purge_stale_context()
                        continue
                    else:
                        self._task_failed = True
                        break

                # 🚫 Hard blocks (unificados)
                hard_block_reason = None
                if tool == "code_analyzer" and file_path:
                    key = f"code_analyzer_{file_path}"
                    tool_usage_count[key] = tool_usage_count.get(key, 0) + 1
                    if tool_usage_count[key] > 1:
                        hard_block_reason = "code_analyzer repetido"

                if tool == "file_reader" and file_path:
                    if "start_line" in args and "end_line" in args:
                        chunk_key = f"file_reader_{file_path}_{args['start_line']}_{args['end_line']}"
                        tool_usage_count[chunk_key] = tool_usage_count.get(chunk_key, 0) + 1
                        if tool_usage_count[chunk_key] > 1:
                            hard_block_reason = "chunk repetido"
                    fully_read_key = f"fully_read_{file_path}"
                    if tool_usage_count.get(fully_read_key, 0) > 0:
                        hard_block_reason = "arquivo já totalmente lido"

                if hard_block_reason:
                    self._emit("hard_block", {"file": file_path, "reason": hard_block_reason})
                    action = self._handle_step_failure(i+1, f"Hard block: {hard_block_reason}", tool, args)
                    if action == "continue":
                        self._purge_stale_context()
                        continue
                    else:
                        self._task_failed = True
                        break

                # Pula chunks impossíveis
                if tool == "file_reader" and "start_line" in args and "end_line" in args and file_path:
                    known_total = None
                    for h in self.agent_state.tool_history:
                        if h["tool"] == "file_reader" and h.get("result", {}).get("total_lines"):
                            h_file = h.get("args", {}).get("file_path") or h.get("args", {}).get("target")
                            if h_file == file_path:
                                known_total = h["result"]["total_lines"]
                                break
                    if known_total and args["start_line"] > known_total:
                        if self.verbose:
                            print(f"[DEBUG] Pulando passo: start_line ({args['start_line']}) > total_lines ({known_total}) para '{file_path}'.")
                        continue

                # Geração de conteúdo para file_writer se necessário
                if tool == "file_writer" and not args.get("content"):
                    generated = None
                    for retry in range(3):
                        generated = self._generate_content(tool, args, objective)
                        if generated:
                            break
                    # Fallback: tenta extrair da última resposta do assistente no histórico
                    if not generated:
                        for msg in reversed(self.session.messages):
                            if msg["role"] == "assistant" and len(msg.get("content", "")) > 20:
                                generated = self._sanitize_error(msg["content"])  # usa sanitização como limpeza
                                if len(generated) > 10:
                                    break
                    if generated:
                        args["content"] = generated
                    else:
                        action = self._handle_step_failure(i+1, "Conteúdo não gerado para file_writer", tool, args)
                        if action == "continue":
                            self._purge_stale_context()
                            continue
                        else:
                            self._task_failed = True
                            break

                # Cache de hash
                cache_hit = False
                if tool in ("code_analyzer", "file_reader") and file_path:
                    try:
                        with open(file_path, 'r', encoding='utf-8') as f:
                            current_hash = hashlib.sha256(f.read().encode('utf-8')).hexdigest()
                    except Exception:
                        current_hash = None

                    stored_hash = self.agent_state.memory.state.get("file_hashes", {}).get(file_path)
                    if current_hash and stored_hash and current_hash == stored_hash:
                        summary = self.agent_state.memory.state.get("file_summaries", {}).get(file_path, "")
                        if summary:
                            self._emit("cache_hit", {"file": file_path, "hash": current_hash[:8]})
                            result = {"ok": True, "done": True, "data": summary, "message": f"Usando cache de {file_path}."}
                            self._emit("tool_end", {"tool": tool, "ok": True})
                            self.agent_state.last_tool = tool
                            self.agent_state.last_args = args
                            self.agent_state.last_result = result
                            self.agent_state.tool_history.append({"tool": tool, "args": args, "result": result})
                            cache_hit = True

                # Exibe diff preview ANTES de modificar o arquivo
                if tool == "file_writer" and args.get("content") and file_path:
                    self._show_diff(file_path, args["content"])

                if not cache_hit:
                    self._emit("tool_start", {"tool": tool, "args": args})
                    result = self._run_tool(tool, args)
                    self._emit("tool_end", {"tool": tool, "ok": result.get("ok")})
                    self._maybe_summarize_and_store(tool, args, result)

                # Ciclo teste‑correção automático
                if tool == "file_writer" and result.get("ok") and file_path.endswith(".py"):
                    if self.verbose:
                        print(f"🧪 [TEST] Iniciando ciclo teste‑correção para '{file_path}'...")
                    if not self._test_and_correct(file_path, objective):
                        # Testes falharam → rollback será acionado no finally
                        self._task_failed = True
                        self._emit("error", {"step": i+1, "error": "Ciclo teste‑correção falhou"})
                        break

                # Verificação de lint pós‑escrita
                if tool == "file_writer" and result.get("ok") and file_path.endswith(".py"):
                    lint_error = self._lint_check(file_path)
                    if lint_error:
                        self._emit("warning", {"step": i+1, "warning": f"Problemas de lint em '{file_path}':\n{lint_error}"})
                        if self.verbose:
                            print(f"⚠️ [LINT] Problemas encontrados em '{file_path}':\n{lint_error}")

                # Marca arquivo completamente lido
                if tool == "file_reader" and result.get("ok") and "total_lines" in result:
                    total_lines = result["total_lines"]
                    end_line = args.get("end_line", total_lines)
                    if end_line == total_lines:
                        fully_read_key = f"fully_read_{file_path}"
                        tool_usage_count[fully_read_key] = 1
                        if self.verbose:
                            print(f"[DEBUG] Arquivo '{file_path}' completamente lido ({total_lines} linhas).")

                # Compressão de contexto
                self._maybe_compress_context()

                if result is not None and not result.get("ok"):
                    action = self._handle_step_failure(i+1, f"Tool '{tool}' falhou: {result.get('error')}", tool, args)
                    if action == "continue":
                        self._purge_stale_context()
                        continue
                    else:
                        self._task_failed = True
                        break

                # Se o objetivo foi uma edição e o file_writer foi bem‑sucedido,
                # encerra com uma resposta curta, sem chamar o LLM.
                if any(kw in objective.lower() for kw in ["mudar", "mude", "alterar", "altere", "corrigir", "corrija", "substituir", "substitua", "editar", "edite", "ajustar", "ajuste"]):
                    if any(h["tool"] == "file_writer" and h.get("result", {}).get("ok") for h in self.agent_state.tool_history):
                        answer = "Arquivo alterado com sucesso."
                        self.agent_state.conversation_history.append({"user": objective, "agent": answer})
                        return answer
            # ----------------------------------------------------------
            # Fase 3: Resposta final (modo texto puro, sem JSON)
            # ----------------------------------------------------------
            notes_content = ""
            if os.path.exists("analysis_notes.md"):
                try:
                    with open("analysis_notes.md", "r", encoding="utf-8") as f:
                        notes_content = f.read(4000)
                except Exception:
                    pass

            # Monta um resumo dos resultados das ferramentas
            tool_results_summary = ""
            for h in self.agent_state.tool_history:
                tool_name = h.get("tool", "")
                result_data = h.get("result", {}).get("data", "")
                if result_data:
                    truncated = str(result_data)[:2000]
                    tool_results_summary += f"\n\n--- Resultado de {tool_name} ---\n{truncated}"

            if notes_content:
                final_prompt = (
                    f"Objetivo: {objective}\n\n"
                    f"Conteúdo das notas de análise:\n```\n{notes_content}\n```\n\n"
                    "Responda ao objetivo do usuário com base nesse conteúdo. "
                    "Não use ferramentas. Apenas texto."
                )
            else:
                final_prompt = (
                    f"Objetivo: {objective}\n\n"
                    "Resultados das ferramentas executadas:\n"
                    f"{tool_results_summary}\n\n"
                    "Responda ao objetivo do usuário com base nesses resultados. "
                    "Não use ferramentas. Apenas texto."
                )

            # Chama o modelo diretamente, sem esperar JSON
            self.session.add_user_message(final_prompt)
            final_payload = self.session.build_payload()
            final_payload["max_tokens"] = 4096
            final_payload["stream"] = False

            try:
                final_response = self.session.send_non_streaming_request(final_payload)
            except Exception as e:
                logger.error(f"Erro na requisição final: {e}")
                final_response = ""

            # Limpa as mensagens temporárias
            self.session.remove_last_user_message()
            if self.session.messages and self.session.messages[-1]["role"] == "assistant":
                self.session.messages.pop()

            if isinstance(final_response, str) and final_response.strip():
                answer = final_response.strip()
                if answer.startswith("{"):
                    try:
                        parsed = json.loads(answer)
                        answer = parsed.get("answer", answer)
                    except Exception:
                        pass
            else:
                answer = "Não foi possível gerar uma resposta final."

            # Pós-validação anti-alucinação (mantida)
            mentioned_files = set(re.findall(r'(?<!\w)[\w\-/]+\.(?:py|json|yaml|yml|md|txt|toml|cfg)(?!\w)', answer))
            read_files = set()
            for h in self.agent_state.tool_history:
                fp = h.get("args", {}).get("file_path") or h.get("args", {}).get("target", "")
                if fp:
                    read_files.add(fp)
            unread = mentioned_files - read_files
            houve_leitura = any(
                h.get("tool") in ("file_reader", "code_analyzer")
                for h in self.agent_state.tool_history
            )
            if unread and houve_leitura:
                answer += "\n\n[⚠️ Aviso: esta análise menciona arquivos que não foram lidos durante a execução: "
                answer += ", ".join(sorted(unread))
                answer += ". As sugestões relacionadas a esses arquivos podem ser imprecisas.]"

            self.agent_state.conversation_history.append({"user": objective, "agent": answer})
            return answer

        finally:
            # Rollback se a tarefa falhou
            if self._task_failed:
                self._rollback()

            while len(self.session.messages) > original_msg_count:
                self.session.messages.pop()
            if len(self.agent_state.conversation_history) > self.agent_state.max_history_turns:
                self.agent_state.conversation_history = self.agent_state.conversation_history[-self.agent_state.max_history_turns:]
            self._maybe_compress_context()
            self.save_memory_to_file("agent_memory.json")
import datetime as dt
import os
import re
from typing import Any, Dict, List, Optional

from agent.error_handler import ErrorHandler
from agent.model_client import ModelClient
from agent.prompts import AGENT_SYSTEM_PROMPT
from agent.state import AgentState
from agent.semantic_memory import SemanticMemory
from logger import logger
from session import ChatSession

CONTEXT_LIMIT = 8192
CONTEXT_COMPRESSION_THRESHOLD = 0.8

# Budgets por tipo de passo
STEP_BUDGETS = {
    "plan": 4096,
    "final": 4096,
    "tool_decision": 2048,
}
DEFAULT_AGENT_MAX_TOKENS = 2048


class ContextManager:
    def __init__(
        self,
        session: ChatSession,
        agent_state: AgentState,
        verbose: bool = False,
    ):
        self.session = session
        self.agent_state = agent_state
        self.verbose = verbose
        self._cached_project_context: Optional[str] = None
        self.model_client = ModelClient()
        self.semantic = SemanticMemory(self.agent_state.memory)

    # ------------------------------------------------------------------
    # Métodos de contexto (inalterados)
    # ------------------------------------------------------------------

    def get_project_context(self) -> str:
        if self._cached_project_context is not None:
            return self._cached_project_context

        ctx = ""
        try:
            import subprocess

            result = subprocess.run(
                ["git", "ls-files", "--others", "--cached", "--exclude-standard"],
                capture_output=True,
                text=True,
                timeout=5,
                cwd=os.getcwd(),
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
                ctx = f"\n\n--- CONTEXTO DO PROJETO ---\nEstrutura raiz:\n" + "\n".join(
                    entries[:40]
                ) + "\n"
            except Exception:
                pass

        self._cached_project_context = ctx
        return ctx

    def estimate_conversation_tokens(self) -> int:
        total_chars = sum(
            len(str(m.get("content", ""))) for m in self.session.messages
        )
        return total_chars // 4

    def maybe_compress_context(self) -> None:
        estimated = self.estimate_conversation_tokens()
        threshold = int(CONTEXT_LIMIT * CONTEXT_COMPRESSION_THRESHOLD)

        if estimated <= threshold:
            return

        if self.verbose:
            print(
                f"⚡ [COMPRESS] Contexto atingiu ~{estimated} tokens (limiar: {threshold}). Comprimindo..."
            )

        compress_prompt = (
            "Resuma a conversa abaixo em um parágrafo denso, mantendo APENAS:\n"
            "- Objetivo original da tarefa\n"
            "- Plano restante (passos já concluídos e pendentes)\n"
            "- Principais descobertas e resultados obtidos\n"
            "- Próximas ações necessárias\n\n"
            "NÃO inclua saudações, repetições ou informações irrelevantes.\n\n"
            "Histórico:\n"
        )
        recent = self.session.messages[-20:]
        for msg in recent:
            compress_prompt += f"[{msg['role']}] {msg['content']}\n"

        original_system = (
            self.session.messages[0]["content"] if self.session.messages else ""
        )

        temp_session = ChatSession("", self.session.config)
        temp_session.set_system_prompt(
            "Resuma o histórico da conversa de forma concisa e técnica."
        )
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
            self.session.messages = [
                {"role": "system", "content": original_system}
            ]
            self.session.add_message("system", f"[RESUMO DO CONTEXTO]: {summary}")
            if self.verbose:
                print(
                    f"✅ [COMPRESS] Contexto comprimido para ~{len(summary)//4} tokens."
                )
        else:
            logger.warning("Resposta vazia ao comprimir contexto.")

    def build_compact_view(self) -> List[Dict[str, Any]]:
        compact = []
        for msg in self.session.messages:
            if msg["role"] == "system":
                compact.append(msg)
                continue

            replaced = False
            for h in self.agent_state.tool_history:
                if h["tool"] == "file_reader" and h.get("result", {}).get("ok"):
                    file_path = h.get("args", {}).get("file_path", "")
                    if file_path and len(msg.get("content", "")) > 500:
                        summary = (
                            self.agent_state.memory.state.get("file_summaries", {})
                            .get(file_path)
                        )
                        if summary:
                            new_msg = msg.copy()
                            new_msg["content"] = (
                                f"[Resumo de '{file_path}']: {summary}"
                            )
                            compact.append(new_msg)
                            replaced = True
                            break
            if not replaced:
                compact.append(msg)

        return compact

    def get_file_hints(self, objective: str) -> str:
        candidates = re.findall(
            r"\b[\w\-.]+\.(?:py|md|txt|json|yaml|yml|toml|cfg)\b", objective
        )
        hints = []
        seen = set()
        for fname in candidates:
            if fname in seen:
                continue
            seen.add(fname)
            path = os.path.join(os.getcwd(), fname)
            if os.path.isfile(path):
                try:
                    with open(path, "r", encoding="utf-8", errors="ignore") as f:
                        line_count = sum(1 for _ in f)
                    hints.append(f"{fname} ({line_count} linhas)")
                except Exception:
                    pass
        try:
            semantic_files = self.semantic.find_similar_files(objective, top_k=5)
            for fname in semantic_files:
                if fname in seen:
                    continue
                seen.add(fname)
                path = os.path.join(os.getcwd(), fname)
                if os.path.isfile(path):
                    try:
                        with open(path, "r", encoding="utf-8", errors="ignore") as f:
                            line_count = sum(1 for _ in f)
                        hints.append(f"{fname} ({line_count} linhas) [semântico]")
                    except Exception:
                        pass
        except Exception:
            pass  # falha silenciosa
        if hints:
            return "\n".join(f"- {h}" for h in hints)
        return ""

    def check_prompt_size(self, context_limit: int = 8192) -> None:
        system_content = self.session.messages[0]["content"]
        estimated_tokens = len(system_content) // 4
        threshold = int(context_limit * 0.8)
        pct = estimated_tokens / context_limit * 100

        if self.verbose:
            print(
                f"📏 [AUDITORIA] Prefixo estimado: ~{estimated_tokens} tokens ({pct:.1f}% do limite de {context_limit})"
            )

        if estimated_tokens > threshold:
            logger.warning(
                f"Prefixo grande: ~{estimated_tokens} tokens ({pct:.1f}%)"
            )
            if self.verbose:
                print(
                    "⚠️  Atenção: prefixo acima de 80%! Considere limpar memória ou reduzir histórico."
                )

    def count_tokens_precise(self, text: str) -> Optional[int]:
        try:
            import requests

            api_url = self.session.config.get(
                "api_url", "http://127.0.0.1:8080/v1/chat/completions"
            )
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

    def build_base_system_prompt(
        self, persona_prompt: str, tools_desc: str
    ) -> str:
        now_str = dt.datetime.now().strftime("%A, %d de %B de %Y %H:%M")
        datetime_context = f"\n\n[SISTEMA] Data e hora atual: {now_str}. Use esta informação para responder perguntas sobre datas."
        project_context = self.get_project_context()
        return (
            persona_prompt
            + "\n\n"
            + AGENT_SYSTEM_PROMPT.format(tools_description=tools_desc)
            + datetime_context
            + project_context
        )

    def build_context(self) -> str:
        analyzed_context = ""
        if self.agent_state.memory.state.get("analyzed_files"):
            analyzed_context = "\n\n--- ARQUIVOS JÁ ANALISADOS ---\n"
            for file, summary in self.agent_state.memory.state[
                "analyzed_files"
            ].items():
                analyzed_context += f"- {file}: {summary}\n"
            analyzed_context += "NÃO reanalise arquivos já listados aqui, a menos que o usuário peça explicitamente.\n"

        memory_context = ""
        if self.agent_state.memory.state:
            memory_context = (
                "\n\n--- SESSION MEMORY ---\n"
                + self.agent_state.memory.stringify()
            )
        memory_context += analyzed_context

        history_context = ""
        if self.agent_state.conversation_history:
            turns = self.agent_state.conversation_history[
                -self.agent_state.max_history_turns :
            ]
            history_context = "\n\n--- HISTÓRICO RECENTE ---\n"
            for turn in turns:
                history_context += (
                    f"Usuário: {turn['user']}\nAgente: {turn['agent']}\n\n"
                )

        return history_context + memory_context

    # ------------------------------------------------------------------
    # Método principal (refatorado — Fix 5)
    # ------------------------------------------------------------------

    def ask_model(
        self,
        prompt: str,
        step_type: str = "tool_decision",
        base_prompt: str = None,
        log_metric_callback=None,
    ) -> Dict[str, Any]:
        """
        Prepara o contexto e delega a comunicação HTTP ao ModelClient.
        """
        original_messages = [m.copy() for m in self.session.messages]
        original_system_content = (
            self.session.messages[0]["content"] if self.session.messages else ""
        )

        if self.verbose:
            self.check_prompt_size()
            exact = self.count_tokens_precise(
                self.session.messages[0]["content"]
            )
            if exact is not None:
                print(f"📏 [AUDITORIA] Tokens exatos: {exact}")

        try:
            context_addition = self.build_context()
            if base_prompt is None:
                base_prompt = self.build_base_system_prompt("", "")

            self.session.messages[0]["content"] = (
                base_prompt + context_addition
            )

            self.session.add_user_message(prompt)

            estimated = self.estimate_conversation_tokens()
            if estimated > int(CONTEXT_LIMIT * 0.75):
                compact_messages = self.build_compact_view()
                original_messages_in_session = self.session.messages
                self.session.messages = compact_messages
                payload = self.session.build_payload()
                self.session.messages = original_messages_in_session
            else:
                payload = self.session.build_payload()

            config_max = self.session.config.get("agent_max_tokens")
            budget = (
                config_max
                if config_max is not None
                else STEP_BUDGETS.get(step_type, DEFAULT_AGENT_MAX_TOKENS)
            )
            payload["max_tokens"] = budget
            payload["stream"] = False

            if self.verbose:
                print(
                    f"⏳ Consultando o modelo (step={step_type}, budget={budget})...",
                    end="",
                    flush=True,
                )

            decision = self.model_client.request(
                session=self.session,
                payload=payload,
                step_type=step_type,
                log_metric_callback=log_metric_callback,
                verbose=self.verbose,
            )

            return decision

        finally:
            self.session.messages = original_messages
            if self.session.messages:
                self.session.messages[0]["content"] = original_system_content

    def purge_stale_context(self) -> None:
        ErrorHandler.purge_stale_context(self.session, self.verbose)
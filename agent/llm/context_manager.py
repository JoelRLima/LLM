import datetime as dt
import os
from collections.abc import Callable
from typing import Any, Dict, List, Optional

from agent.error_handler import ErrorHandler
from agent.llm.context_views import (
    build_compact_view,
    compress_conversation,
    discover_project_context,
    get_file_hints,
)
from agent.llm.grammars import AUTO_GRAMMAR, AutoGrammar, get_grammar
from agent.llm.model_client import ModelClient
from agent.llm.prompts import AGENT_SYSTEM_PROMPT
from agent.llm.session import ChatSession
from agent.memory.semantic_memory import SemanticMemory
from agent.runtime.hardware import resolve_hardware_profile
from agent.runtime.logging import logger
from agent.state import AgentState

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
        self.hardware_profile = resolve_hardware_profile(self.session.config)
        self._cached_project_context: Optional[str] = None
        self.model_client = ModelClient()
        self.semantic = SemanticMemory(self.agent_state.memory)

    # ------------------------------------------------------------------
    # Métodos de contexto (inalterados)
    # ------------------------------------------------------------------

    def get_project_context(self) -> str:
        if self._cached_project_context is not None:
            return self._cached_project_context
        self._cached_project_context = discover_project_context(os.getcwd())
        return self._cached_project_context

    def estimate_conversation_tokens(self) -> int:
        total_chars = sum(
            len(str(m.get("content", ""))) for m in self.session.messages
        )
        return total_chars // 4

    def maybe_compress_context(self) -> None:
        compress_conversation(self.session, self.hardware_profile.context_limit, self.verbose)

    def build_compact_view(self) -> List[Dict[str, Any]]:
        return build_compact_view(
            self.session.messages,
            self.agent_state.tool_history,
            self.agent_state.memory.state,
        )

    def get_file_hints(self, objective: str) -> str:
        return get_file_hints(objective, self.semantic)

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
            count = self.session.gateway.count_tokens(text)
            return int(count) if count is not None else None
        except Exception as e:
            logger.warning(f"Não foi possível contar tokens pelo provider: {e}")
            return None

    def build_base_system_prompt(
        self, persona_prompt: str, tools_desc: str
    ) -> str:
        now_str = dt.datetime.now().strftime("%A, %d de %B de %Y %H:%M")
        datetime_context = f"\n\n[SISTEMA] Data e hora atual: {now_str}. Use esta informação para responder perguntas sobre datas."
        project_context = str(self.get_project_context())
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
        base_prompt: str | None = None,
        log_metric_callback: Callable[[Dict[str, Any]], None] | None = None,
        grammar: str | None | AutoGrammar = AUTO_GRAMMAR,
    ) -> Dict[str, Any]:
        """
        Prepara o contexto e delega a comunicação HTTP ao ModelClient.

        Args:
            grammar: gramática GBNF a usar. Por padrão (AUTO_GRAMMAR), a
                gramática é escolhida automaticamente com base em
                `step_type`. Passe uma string para sobrescrever, ou None
                para desabilitar a gramática nesta chamada.
        """
        if isinstance(grammar, AutoGrammar):
            effective_grammar = get_grammar(step_type)
        else:
            effective_grammar = grammar
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
            if estimated > int(self.hardware_profile.context_limit * 0.75):
                compact_messages = self.build_compact_view()
                original_messages_in_session = self.session.messages
                self.session.messages = compact_messages
                payload = self.session.build_payload()
                self.session.messages = original_messages_in_session
            else:
                payload = self.session.build_payload()

            config_max = self.session.config.get("agent_max_tokens")
            budget = config_max if config_max is not None else min(
                STEP_BUDGETS.get(step_type, DEFAULT_AGENT_MAX_TOKENS),
                self.hardware_profile.default_output_tokens,
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
                grammar=effective_grammar,
            )

            return decision

        finally:
            self.session.messages = original_messages
            if self.session.messages:
                self.session.messages[0]["content"] = original_system_content

    def purge_stale_context(self) -> None:
        ErrorHandler.purge_stale_context(self.session, self.verbose)

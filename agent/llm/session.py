import json
from typing import Any, Callable, Dict, List, Optional, Tuple

from agent.cancellation import CancellationToken
from agent.llm.contracts import (
    ModelGateway,
    ModelRequest,
    ModelResponse,
)
from agent.llm.decision_contract import ModelRequestContract
from agent.llm.model_profile import ResolvedModelProfile, resolve_gateway_model_profile
from agent.llm.providers import create_model_gateway
from agent.runtime.budget import TaskBudgetLedger
from agent.runtime.hardware import HardwareProfile, resolve_hardware_profile
from agent.runtime.logging import logger


class ChatSession:
    """Gerencia o histórico, o orçamento de pensamento e a comunicação com o servidor."""

    def __init__(
        self,
        system_prompt: str,
        config: Dict[str, Any],
        gateway: Optional[ModelGateway] = None,
        budget_ledger: TaskBudgetLedger | None = None,
    ) -> None:
        self.messages: List[Dict[str, str]] = [{"role": "system", "content": system_prompt}]
        self.thinking_budget: int = 0
        self.config: Dict[str, Any] = config
        self.gateway: ModelGateway = gateway or create_model_gateway(config)
        self.model_profile: ResolvedModelProfile = resolve_gateway_model_profile(
            config,
            self.gateway,
        )
        self.budget_ledger = budget_ledger or TaskBudgetLedger.from_config(config)
        self.cancellation_token = CancellationToken()
        self.task_policy: Any = None
        self.hardware_profile: HardwareProfile = resolve_hardware_profile(config)
        self.model_call_callback: Callable[[Dict[str, Any]], None] | None = None
        self._grammar_supports_grammar: Optional[bool] = None

    def set_model_call_callback(
        self, callback: Callable[[Dict[str, Any]], None] | None
    ) -> None:
        """Instala o observador de cada request real do provider."""
        self.model_call_callback = callback
    def set_system_prompt(self, prompt: str) -> None:
        """Substitui o system prompt base."""
        self.messages[0]["content"] = prompt
    def get_effective_system_prompt(self) -> str:
        """Retorna o prompt com a instrução de pensamento, se ativo."""
        if self.thinking_budget > 0:
            return (
                self.messages[0]["content"]
                + f"\n\n[THINKING]: You may spend up to {self.thinking_budget} tokens thinking. "
                "This is a maximum limit, not a target. Stop as soon as you have a satisfactory answer. "
                "Be concise."
            )
        return self.messages[0]["content"]
    def add_message(self, role: str, content: str) -> None:
        """Adiciona uma mensagem com role arbitrário (user, assistant, tool, function, etc.)."""
        self.messages.append({"role": role, "content": content})
    def add_user_message(self, content: str) -> None:
        self.add_message("user", content)
    def add_assistant_message(self, content: str) -> None:
        self.add_message("assistant", content)
    def remove_last_user_message(self) -> None:
        """Remove a última mensagem do usuário (usado quando a requisição falha)."""
        if self.messages and self.messages[-1]["role"] == "user":
            self.messages.pop()
    def clear_history(self) -> None:
        """Mantém apenas o system prompt."""
        self.messages = [{"role": "system", "content": self.messages[0]["content"]}]
    def save_to_file(self, caminho: str = "chat_history.json") -> Tuple[bool, str]:
        """Salva o histórico completo em um arquivo JSON."""
        try:
            with open(caminho, "w", encoding="utf-8") as f:
                json.dump(self.messages, f, ensure_ascii=False, indent=2)
            logger.info(f"Histórico salvo em {caminho}")
            return True, ""
        except Exception as e:
            logger.error(f"Erro ao salvar histórico em {caminho}: {e}")
            return False, str(e)
    def load_from_file(self, caminho: str = "chat_history.json") -> Tuple[bool, str]:
        """Carrega o histórico de um arquivo JSON, substituindo o atual."""
        try:
            with open(caminho, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, list):
                return False, "Formato inválido (esperado lista de mensagens)."
            for msg in data:
                if not isinstance(msg, dict) or "role" not in msg or "content" not in msg:
                    return False, "Mensagens devem ter 'role' e 'content'."
            self.messages = data
            logger.info(f"Histórico carregado de {caminho}")
            return True, ""
        except FileNotFoundError:
            return False, f"Arquivo '{caminho}' não encontrado."
        except Exception as e:
            logger.error(f"Erro ao carregar histórico de {caminho}: {e}")
            return False, str(e)
    def build_request(
        self,
        response_format: Optional[str] = None,
        grammar: Optional[str] = None,
        *,
        stream: bool = True,
        max_output_tokens: int | None = None,
        request_contract: ModelRequestContract | str | None = None,
    ) -> ModelRequest:
        from agent.llm.session_requests import build_model_request

        return build_model_request(
            self,
            response_format,
            grammar,
            stream=stream,
            max_output_tokens=max_output_tokens,
            request_contract=request_contract,
        )

    def complete_request(self, request: ModelRequest) -> ModelResponse:
        """Complete one canonical request with task-budget accounting."""
        from agent.llm.session_requests import complete_model_request

        return complete_model_request(self, request)

    def consume_stream_request(
        self,
        request: ModelRequest,
        callbacks: Dict[str, Callable[..., Any]],
    ) -> str:
        """Consume a canonical stream with the same accounting as completion."""
        from agent.llm.session_requests import consume_model_stream

        return consume_model_stream(self, request, callbacks)

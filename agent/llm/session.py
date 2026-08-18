import json
import time
from typing import Any, Callable, Dict, List, Optional, Tuple, cast

from agent.llm.contracts import (
    LegacyPayloadGateway,
    ModelConnectionError,
    ModelMessage,
    ModelRequest,
    ModelTimeoutError,
    PendingStream,
    StructuredOutputMode,
    StructuredOutputRequest,
    build_model_call_metric,
    response_text,
    response_usage,
)
from agent.llm.providers import create_model_gateway
from agent.runtime.budget import TaskBudgetLedger, estimate_payload_tokens
from agent.runtime.hardware import HardwareProfile, resolve_hardware_profile
from agent.runtime.logging import logger

SessionTimeoutError = ModelTimeoutError
SessionConnectionError = ModelConnectionError


class ChatSession:
    """Gerencia o histórico, o orçamento de pensamento e a comunicação com o servidor."""

    def __init__(
        self,
        system_prompt: str,
        config: Dict[str, Any],
        gateway: Optional[LegacyPayloadGateway] = None,
        budget_ledger: TaskBudgetLedger | None = None,
    ) -> None:
        self.messages: List[Dict[str, str]] = [{"role": "system", "content": system_prompt}]
        self.thinking_budget: int = 0
        self.config: Dict[str, Any] = config
        self.gateway: LegacyPayloadGateway = gateway or create_model_gateway(config)
        self.budget_ledger = budget_ledger or TaskBudgetLedger.from_config(config)
        self.hardware_profile: HardwareProfile = resolve_hardware_profile(config)
        self.model_call_callback: Callable[[Dict[str, Any]], None] | None = None
        self._grammar_supports_grammar: Optional[bool] = None

    def set_model_call_callback(
        self, callback: Callable[[Dict[str, Any]], None] | None
    ) -> None:
        """Instala o observador de cada request real do provider."""
        self.model_call_callback = callback
    def _record_model_call(
        self,
        started_at: float,
        *,
        success: bool,
        streaming: bool,
        response: Any = None,
        call_number: int | None = None,
        estimated_tokens: int = 0,
    ) -> None:
        callback = self.model_call_callback
        if callback is None:
            return
        entry = build_model_call_metric(
            self.gateway,
            self.config,
            started_at,
            success=success,
            streaming=streaming,
            response=response,
            call_number=call_number,
            estimated_tokens=estimated_tokens,
        )
        try:
            callback(entry)
        except Exception as exc:  # observability must not change provider semantics
            logger.warning("Falha ao registrar chamada do modelo: %s", type(exc).__name__)
    def _finalize_model_call(
        self,
        call_number: int,
        started_at: float,
        *,
        success: bool,
        streaming: bool,
        response: Any = None,
        usage: Any = None,
        estimated_tokens: int = 0,
    ) -> None:
        self.budget_ledger.finalize_model_call(
            call_number, usage=usage, estimated_tokens=estimated_tokens
        )
        self._record_model_call(
            started_at,
            success=success,
            streaming=streaming,
            response=response,
            call_number=call_number,
            estimated_tokens=estimated_tokens,
        )
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
    def build_payload(
        self,
        response_format: Optional[str] = None,
        grammar: Optional[str] = None,
    ) -> Dict[str, Any]:
        system_content = self.get_effective_system_prompt()
        if response_format:
            system_content += "\n\n" + response_format

        payload_messages = [{"role": "system", "content": system_content}] + self.messages[1:]
        structured = None
        if grammar is not None:
            structured = StructuredOutputRequest(
                mode=StructuredOutputMode.GBNF,
                grammar=grammar,
            )
        request = ModelRequest(
            messages=tuple(
                ModelMessage(role=message["role"], content=message["content"])
                for message in payload_messages
            ),
            model=str(getattr(self.gateway, "model", self.config.get("model", "default"))),
            temperature=float(
                getattr(self.gateway, "profile", {}).get(
                    "temperature", self.config.get("temperature", 0.6)
                )
            ),
            max_output_tokens=int(
                getattr(self.gateway, "profile", {}).get(
                    "max_tokens",
                    self.config.get(
                        "max_tokens", self.hardware_profile.default_output_tokens
                    ),
                )
            ),
            stream=True,
            reasoning_budget=self.thinking_budget,
            structured_output=structured,
        )
        return cast(Dict[str, Any], self.gateway.build_payload(request))

    def send_request(self, payload: Dict[str, Any], stream: bool = True) -> Any:
        """Fachada legada; transporte pertence ao adapter de provider."""
        call_number = self.budget_ledger.reserve_model_call()
        started_at = time.monotonic()
        try:
            response = self.gateway.send_payload(payload, stream=stream)
        except Exception:
            estimate = estimate_payload_tokens(payload)
            self._finalize_model_call(
                call_number,
                started_at,
                success=False,
                streaming=stream,
                estimated_tokens=estimate,
            )
            raise
        if stream:
            return PendingStream(response, call_number, payload, started_at)
        usage = response_usage(response)
        estimate = estimate_payload_tokens(payload)
        self._finalize_model_call(
            call_number,
            started_at,
            success=True,
            streaming=stream,
            response=response,
            usage=usage,
            estimated_tokens=estimate,
        )
        return response

    def send_non_streaming_request(self, payload: Dict[str, Any]) -> str:
        """Envia sem streaming e retorna o texto, propagando falhas do adapter."""
        call_number = self.budget_ledger.reserve_model_call()
        started_at = time.monotonic()
        try:
            response = self.gateway.complete_payload(payload)
        except Exception:
            estimate = estimate_payload_tokens(payload)
            self._finalize_model_call(
                call_number,
                started_at,
                success=False,
                streaming=False,
                estimated_tokens=estimate,
            )
            raise
        usage = response_usage(response)
        content = response_text(response)
        estimate = estimate_payload_tokens(payload, content)
        self._finalize_model_call(
            call_number,
            started_at,
            success=True,
            streaming=False,
            response=response,
            usage=usage,
            estimated_tokens=estimate,
        )
        return content

    def process_stream(self, response: Any, callbacks: Dict[str, Callable]) -> str:
        """Consome o stream e encaminha chunks aos callbacks fornecidos."""
        if not isinstance(response, PendingStream):
            return cast(str, self.gateway.consume_stream(response, callbacks))

        usage: Any = None

        def capture_usage(value: Any) -> None:
            nonlocal usage
            usage = value
            callback = callbacks.get("on_usage")
            if callback is not None:
                callback(value)

        stream_callbacks = dict(callbacks)
        stream_callbacks["on_usage"] = capture_usage
        visible = ""
        try:
            visible = cast(
                str,
                self.gateway.consume_stream(response.response, stream_callbacks),
            )
        except Exception:
            estimate = estimate_payload_tokens(response.payload, visible)
            self._finalize_model_call(
                response.call_number,
                response.started_at,
                success=False,
                streaming=True,
                estimated_tokens=estimate,
            )
            raise
        estimate = estimate_payload_tokens(response.payload, visible)
        observed = {"usage": usage} if usage is not None else visible
        self._finalize_model_call(
            response.call_number,
            response.started_at,
            success=True,
            streaming=True,
            response=observed,
            usage=usage,
            estimated_tokens=estimate,
        )
        return visible

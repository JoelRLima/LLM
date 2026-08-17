import json
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple, cast

from agent.llm.contracts import (
    LegacyPayloadGateway,
    ModelConnectionError,
    ModelMessage,
    ModelRequest,
    ModelTimeoutError,
    StructuredOutputMode,
    StructuredOutputRequest,
)
from agent.llm.providers import create_model_gateway
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
    ) -> None:
        self.messages: List[Dict[str, str]] = [{"role": "system", "content": system_prompt}]
        self.thinking_budget: int = 0
        self.config: Dict[str, Any] = config
        self.gateway: LegacyPayloadGateway = gateway or create_model_gateway(config)
        self.hardware_profile: HardwareProfile = resolve_hardware_profile(config)
        self.model_call_callback: Callable[[Dict[str, Any]], None] | None = None
        self._grammar_supports_grammar: Optional[bool] = None

    def set_model_call_callback(
        self, callback: Callable[[Dict[str, Any]], None] | None
    ) -> None:
        """Instala o observador da fronteira legada de provider.

        O callback pertence ao transporte (e não ao parser/model client),
        portanto cada request real é publicado uma única vez, inclusive em
        falhas e streams.
        """
        self.model_call_callback = callback

    def _record_model_call(
        self, started_at: float, *, success: bool, streaming: bool, response: Any = None
    ) -> None:
        callback = self.model_call_callback
        if callback is None:
            return
        entry: Dict[str, Any] = {
            "type": "model_call",
            "metric_type": "model_call",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "duration_ms": max(0, int((time.monotonic() - started_at) * 1000)),
            "success": bool(success),
            "streaming": bool(streaming),
            "provider": getattr(self.gateway, "provider_name", None),
            "model": getattr(self.gateway, "model", self.config.get("model")),
        }
        usage = response.get("usage") if isinstance(response, dict) else None
        if isinstance(usage, dict):
            for source, target in (
                ("prompt_tokens", "prompt_tokens"),
                ("completion_tokens", "completion_tokens"),
                ("total_tokens", "total_tokens"),
            ):
                value = usage.get(source)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    entry[target] = int(value)
        try:
            callback(entry)
        except Exception as exc:  # observability must not change provider semantics
            logger.warning("Falha ao registrar chamada do modelo: %s", type(exc).__name__)

    # ---- Gerenciamento de prompts ----

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

    # ---- Histórico (mensagens de qualquer role) ----

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

    # ---- Salvar / Carregar ----

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

    # ---- Construção de payloads ----

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

    # ---- Envio de requisições ----

    def send_request(self, payload: Dict[str, Any], stream: bool = True) -> Any:
        """Fachada legada; transporte pertence ao adapter de provider."""
        started_at = time.monotonic()
        try:
            response = self.gateway.send_payload(payload, stream=stream)
        except Exception:
            self._record_model_call(started_at, success=False, streaming=stream)
            raise
        self._record_model_call(
            started_at, success=True, streaming=stream, response=response
        )
        return response

    def send_non_streaming_request(self, payload: Dict[str, Any]) -> str:
        """
        Envia uma requisição sem streaming e retorna o texto da resposta.
        Levanta exceções em caso de erro (timeout, HTTPError, etc.).
        """
        started_at = time.monotonic()
        try:
            response = self.gateway.complete_payload(payload)
        except Exception:
            self._record_model_call(started_at, success=False, streaming=False)
            raise
        self._record_model_call(
            started_at, success=True, streaming=False, response=response
        )
        return cast(str, response)

    # ---- Processamento de stream (mantido) ----

    def process_stream(self, response: Any, callbacks: Dict[str, Callable]) -> str:
        """
        Itera sobre as linhas do stream e chama callbacks apropriados.

        callbacks (todos opcionais):
            on_raw_line(line_str)       – linha bruta recebida
            on_thinking_chunk(text)     – trecho de raciocínio
            on_content_chunk(text)      – trecho da resposta final
            on_error(message)           – erro reportado pelo servidor
            on_done(timings)            – timings finais (último chunk)
        """
        return cast(str, self.gateway.consume_stream(response, callbacks))

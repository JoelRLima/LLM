"""Contratos independentes de provider para chamadas de modelo."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, Iterator, Optional, Protocol, Sequence

from agent.llm.decision_contract import ModelRequestContract, request_contract_value
from agent.llm.errors import (
    ModelConnectionError as ModelConnectionError,
)
from agent.llm.errors import (
    ModelGatewayError as ModelGatewayError,
)
from agent.llm.errors import (
    ModelProviderError as ModelProviderError,
)
from agent.llm.errors import (
    ModelResponseError as ModelResponseError,
)
from agent.llm.errors import (
    ModelTimeoutError as ModelTimeoutError,
)
from agent.llm.errors import (
    UnsupportedModelCapability as UnsupportedModelCapability,
)


class StructuredOutputMode(str, Enum):
    NONE = "none"
    AUTO = "auto"
    JSON_SCHEMA = "json_schema"
    GBNF = "gbnf"
    JSON_PROMPT = "json_prompt"
@dataclass(frozen=True)
class ProviderCapabilities:
    """Recursos efetivamente oferecidos por um perfil de backend."""
    streaming: bool = True
    structured_output_modes: tuple[StructuredOutputMode, ...] = (
        StructuredOutputMode.JSON_PROMPT,
    )
    reasoning: bool = False
    token_counting: bool = False
    tool_calls: bool = False

    def to_dict(self) -> Dict[str, Any]:
        """Project typed capabilities for compatibility/reporting edges."""

        return {
            "streaming": self.streaming,
            "structured_output": (
                self.structured_output_modes[0].value
                if self.structured_output_modes
                else "none"
            ),
            "structured_output_modes": [
                mode.value if isinstance(mode, StructuredOutputMode) else str(mode)
                for mode in self.structured_output_modes
            ],
            "reasoning": self.reasoning,
            "token_counting": self.token_counting,
            "tool_calls": self.tool_calls,
        }
    def supports(self, mode: StructuredOutputMode) -> bool:
        return mode in self.structured_output_modes
@dataclass(frozen=True)
class ModelMessage:
    role: str
    content: str
@dataclass(frozen=True)
class StructuredOutputRequest:
    mode: StructuredOutputMode = StructuredOutputMode.AUTO
    schema: Optional[Dict[str, Any]] = None
    grammar: Optional[str] = None
    instruction: Optional[str] = None
@dataclass(frozen=True)
class ModelRequest:
    messages: Sequence[ModelMessage]
    model: str
    temperature: float
    max_output_tokens: int
    stream: bool = False
    reasoning_budget: int = 0
    structured_output: Optional[StructuredOutputRequest] = None
    provider_options: Dict[str, Any] = field(default_factory=dict)
    context_compacted: bool = False
    context_limit: int | None = None
    request_contract: ModelRequestContract | None = None
    @property
    def request_contract_id(self) -> str | None:
        """Return the stable serialized value of the request contract."""
        return request_contract_value(self.request_contract)
@dataclass(frozen=True)
class TokenUsage:
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    available: bool = True
def normalize_usage(
    usage: Any,
) -> tuple[int | None, int | None, int | None, int | None, bool]:
    if usage is None:
        return None, None, None, None, False
    available = (
        usage.get("available", True)
        if isinstance(usage, Mapping)
        else getattr(usage, "available", True)
    )
    if available is False:
        return None, None, None, None, False
    def value(primary: str, legacy: str) -> int | None:
        raw = (
            usage.get(primary, usage.get(legacy))
            if isinstance(usage, Mapping)
            else getattr(usage, primary, None)
        )
        if raw is None and not isinstance(usage, Mapping):
            raw = getattr(usage, legacy, None)
        return raw if isinstance(raw, int) and not isinstance(raw, bool) and raw >= 0 else None
    input_tokens = value("input_tokens", "prompt_tokens")
    output_tokens = value("output_tokens", "completion_tokens")
    total_tokens = value("total_tokens", "total_tokens")
    if total_tokens is not None:
        return input_tokens, output_tokens, total_tokens, total_tokens, True
    if input_tokens is not None and output_tokens is not None:
        return input_tokens, output_tokens, total_tokens, input_tokens + output_tokens, True
    return input_tokens, output_tokens, total_tokens, None, False
@dataclass(frozen=True)
class ModelResponse:
    content: str
    reasoning: str = ""
    usage: TokenUsage = field(default_factory=lambda: TokenUsage(available=False))
    finish_reason: Optional[str] = None
    provider_metadata: Dict[str, Any] = field(default_factory=dict)
class PendingStream:
    """Legacy stream response carrying its task-budget reservation."""
    __slots__ = (
        "response",
        "call_number",
        "payload",
        "started_at",
        "request",
        "request_input_measurement",
        "service",
        "operation",
    )
    def __init__(
        self,
        response: Any,
        call_number: int,
        payload: Dict[str, Any],
        started_at: float,
        request: Any = None,
        request_input_measurement: Any = None,
        service: Any = None,
        operation: str | None = None,
    ) -> None:
        self.response = response
        self.call_number = call_number
        self.payload = payload
        self.started_at = started_at
        self.request = request
        self.request_input_measurement = request_input_measurement
        self.service = service
        self.operation = operation
    def __getattr__(self, name: str) -> Any:
        return getattr(self.response, name)
def response_usage(response: Any) -> Any:
    if isinstance(response, ModelResponse):
        return response.usage
    return response.get("usage") if isinstance(response, Mapping) else None
def response_text(response: Any) -> str:
    if isinstance(response, ModelResponse):
        return response.content
    if isinstance(response, Mapping):
        content = response.get("content")
        if isinstance(content, str):
            return content
    return response if isinstance(response, str) else str(response)


def build_model_call_metric(*args: Any, **kwargs: Any) -> Dict[str, Any]:
    """Compatibility facade for the canonical model-call metric projection."""

    from agent.llm.model_metrics import build_model_call_metric as project_metric

    return project_metric(*args, **kwargs)


class StreamEventType(str, Enum):
    CONTENT = "content"
    REASONING = "reasoning"
    USAGE = "usage"
    ERROR = "error"
    DONE = "done"
@dataclass(frozen=True)
class StreamEvent:
    type: StreamEventType
    text: str = ""
    data: Dict[str, Any] = field(default_factory=dict)
class ModelGateway(Protocol):
    @property
    def capabilities(self) -> ProviderCapabilities:
        ...
    @property
    def provider_name(self) -> str:
        ...
    def complete(self, request: ModelRequest) -> ModelResponse:
        ...
    def stream(self, request: ModelRequest) -> Iterator[StreamEvent]:
        ...
    def measure_request_input_tokens(self, request: ModelRequest) -> Any:
        ...
    def count_tokens(self, text: str) -> Optional[int]:
        ...
class LegacyPayloadGateway(ModelGateway, Protocol):
    """Compatibilidade temporária para os consumidores do antigo `ChatSession`.
    Casos de uso novos devem usar apenas `ModelGateway`.
    """
    def build_payload(self, request: ModelRequest) -> Dict[str, Any]:
        ...
    def send_payload(self, payload: Dict[str, Any], stream: bool) -> Any:
        ...
    def complete_payload(self, payload: Dict[str, Any]) -> str | ModelResponse:
        ...
    def consume_stream(self, response: Any, callbacks: Dict[str, Callable[..., Any]]) -> str:
        ...
class UnavailableModelGateway:
    """Gateway explícito para casos de uso determinísticos sem modelo.
    Ele permite construir um ``TaskExecutionContext`` para análise/review sem
    fingir que existe um backend. Qualquer tentativa de geração falha fechada.
    """
    provider_name = "unavailable"
    capabilities = ProviderCapabilities(
        streaming=False,
        structured_output_modes=(),
        reasoning=False,
        token_counting=False,
        tool_calls=False,
    )
    def complete(self, request: ModelRequest) -> ModelResponse:
        del request
        raise UnsupportedModelCapability("Esta operação exige um ModelGateway configurado.")
    def stream(self, request: ModelRequest) -> Iterator[StreamEvent]:
        del request
        raise UnsupportedModelCapability("Esta operação exige um ModelGateway configurado.")
    def measure_request_input_tokens(self, request: ModelRequest) -> None:
        del request
        return None
    def count_tokens(self, text: str) -> Optional[int]:
        del text
        return None

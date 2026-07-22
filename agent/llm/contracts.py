"""Contratos independentes de provider para chamadas de modelo."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, Iterator, Optional, Protocol, Sequence


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


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


@dataclass(frozen=True)
class ModelResponse:
    content: str
    reasoning: str = ""
    usage: TokenUsage = field(default_factory=TokenUsage)
    finish_reason: Optional[str] = None
    provider_metadata: Dict[str, Any] = field(default_factory=dict)


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

    def complete_payload(self, payload: Dict[str, Any]) -> str:
        ...

    def consume_stream(self, response: Any, callbacks: Dict[str, Callable[..., Any]]) -> str:
        ...


class ModelGatewayError(RuntimeError):
    """Erro normalizado na fronteira de provider."""


class ModelTimeoutError(ModelGatewayError, TimeoutError):
    pass


class ModelConnectionError(ModelGatewayError, ConnectionError):
    def __init__(self, message: str, response: Any = None) -> None:
        super().__init__(message)
        self.response = response


class ModelResponseError(ModelGatewayError, ValueError):
    pass


class UnsupportedModelCapability(ModelGatewayError):
    pass


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

    def count_tokens(self, text: str) -> Optional[int]:
        del text
        return None

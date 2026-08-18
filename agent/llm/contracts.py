"""Contratos independentes de provider para chamadas de modelo."""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
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
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    available: bool = True


@dataclass(frozen=True)
class ModelResponse:
    content: str
    reasoning: str = ""
    usage: TokenUsage = field(default_factory=lambda: TokenUsage(available=False))
    finish_reason: Optional[str] = None
    provider_metadata: Dict[str, Any] = field(default_factory=dict)


class PendingStream:
    """Legacy stream response carrying its task-budget reservation."""

    __slots__ = ("response", "call_number", "payload", "started_at")

    def __init__(self, response: Any, call_number: int, payload: Dict[str, Any], started_at: float) -> None:
        self.response = response
        self.call_number = call_number
        self.payload = payload
        self.started_at = started_at

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


def build_model_call_metric(
    gateway: Any,
    config: Mapping[str, Any],
    started_at: float,
    *,
    success: bool,
    streaming: bool,
    response: Any = None,
    call_number: int | None = None,
    estimated_tokens: int = 0,
) -> Dict[str, Any]:
    usage = response_usage(response)
    available = usage is not None and (
        usage.get("available", True) is not False
        if isinstance(usage, Mapping)
        else getattr(usage, "available", True) is not False
    )

    def value(name: str, fallback: str | None = None) -> int | None:
        raw = usage.get(name) if isinstance(usage, Mapping) else getattr(usage, name, None)
        if raw is None and fallback is not None:
            raw = usage.get(fallback) if isinstance(usage, Mapping) else getattr(usage, fallback, None)
        return int(raw) if isinstance(raw, (int, float)) and not isinstance(raw, bool) and raw >= 0 else None

    input_tokens = value("input_tokens", "prompt_tokens") if available else None
    output_tokens = value("output_tokens", "completion_tokens") if available else None
    total_tokens = value("total_tokens") if available else None
    complete = input_tokens is not None and output_tokens is not None
    entry: Dict[str, Any] = {
        "type": "model_call",
        "metric_type": "model_call",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "duration_ms": max(0, int((time.monotonic() - started_at) * 1000)),
        "success": bool(success),
        "streaming": bool(streaming),
        "provider": getattr(gateway, "provider_name", None),
        "model": getattr(gateway, "model", config.get("model")),
        "token_usage_complete": complete,
    }
    if call_number is not None:
        entry["call_number"] = call_number
    if input_tokens is not None:
        entry["input_tokens"] = entry["prompt_tokens"] = input_tokens
    if output_tokens is not None:
        entry["output_tokens"] = entry["completion_tokens"] = output_tokens
    if total_tokens is not None:
        entry["total_tokens"] = total_tokens
    if complete:
        assert input_tokens is not None and output_tokens is not None
        entry["estimated_tokens"] = 0
        entry["accounted_tokens"] = total_tokens if total_tokens is not None else input_tokens + output_tokens
    else:
        entry["estimated_tokens"] = max(0, estimated_tokens)
        entry["accounted_tokens"] = max(0, estimated_tokens)
    return entry


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

    def complete_payload(self, payload: Dict[str, Any]) -> str | ModelResponse:
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

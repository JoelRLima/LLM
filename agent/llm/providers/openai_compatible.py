"""Adapter para endpoints OpenAI-compatible de Chat Completions.

Todos os detalhes de HTTP, `choices`, SSE, GBNF, `chat_template_kwargs` e
`/tokenize` ficam confinados neste módulo.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, Iterator, Optional

import requests
from requests import Response
from requests.exceptions import HTTPError, RequestException, Timeout

from agent.llm.contracts import (
    ModelConnectionError,
    ModelRequest,
    ModelResponse,
    ModelResponseError,
    ModelTimeoutError,
    ProviderCapabilities,
    StreamEvent,
    StreamEventType,
    StructuredOutputMode,
    TokenUsage,
    UnsupportedModelCapability,
)
from agent.runtime.logging import logger


def _structured_modes(raw: Any) -> tuple[StructuredOutputMode, ...]:
    value = str(raw or "json_prompt").lower()
    modes: list[StructuredOutputMode] = []
    if value == "json_schema":
        modes.append(StructuredOutputMode.JSON_SCHEMA)
    elif value == "gbnf":
        modes.append(StructuredOutputMode.GBNF)
    modes.append(StructuredOutputMode.JSON_PROMPT)
    return tuple(modes)


class OpenAICompatibleGateway:
    provider_name = "openai_compatible"

    def __init__(self, profile: Dict[str, Any]) -> None:
        self.profile = dict(profile)
        self.model = str(profile.get("model", "default"))
        self.timeout = float(profile.get("timeout", 300))
        self.provider_options = dict(profile.get("provider_options") or {})
        self.api_url = self._resolve_api_url(profile)
        raw_capabilities = profile.get("capabilities") or {}
        self._capabilities = ProviderCapabilities(
            streaming=bool(raw_capabilities.get("streaming", True)),
            structured_output_modes=_structured_modes(
                raw_capabilities.get("structured_output", "json_prompt")
            ),
            reasoning=bool(raw_capabilities.get("reasoning", False)),
            token_counting=bool(raw_capabilities.get("token_counting", False)),
            tool_calls=bool(raw_capabilities.get("tool_calls", False)),
        )

    @staticmethod
    def _resolve_api_url(profile: Dict[str, Any]) -> str:
        if profile.get("api_url"):
            return str(profile["api_url"])
        base_url = str(profile.get("base_url", "http://127.0.0.1:8080/v1")).rstrip("/")
        return f"{base_url}/chat/completions"

    @property
    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    def build_payload(self, request: ModelRequest) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "model": request.model or self.model,
            "messages": [
                {"role": message.role, "content": message.content}
                for message in request.messages
            ],
            "temperature": request.temperature,
            "max_tokens": request.max_output_tokens,
            "stream": request.stream,
        }

        reasoning_mode = self.provider_options.get("reasoning_mode")
        if reasoning_mode == "chat_template_kwargs" and self.capabilities.reasoning:
            payload["chat_template_kwargs"] = {
                "enable_thinking": request.reasoning_budget > 0,
                "thinking_budget": max(0, request.reasoning_budget),
            }

        structured = request.structured_output
        if structured is not None:
            mode = structured.mode
            if mode == StructuredOutputMode.AUTO:
                mode = self.capabilities.structured_output_modes[0]
            if mode == StructuredOutputMode.GBNF:
                if not self.capabilities.supports(StructuredOutputMode.GBNF):
                    raise UnsupportedModelCapability("O provider não suporta GBNF.")
                if structured.grammar:
                    payload["grammar"] = structured.grammar
            elif mode == StructuredOutputMode.JSON_SCHEMA:
                if not self.capabilities.supports(StructuredOutputMode.JSON_SCHEMA):
                    raise UnsupportedModelCapability("O provider não suporta JSON Schema nativo.")
                if structured.schema:
                    payload["response_format"] = {
                        "type": "json_schema",
                        "json_schema": {"name": "agent_response", "schema": structured.schema},
                    }
        payload.update(request.provider_options)
        return payload

    def send_payload(self, payload: Dict[str, Any], stream: bool) -> Response:
        payload_with_stream = {**payload, "stream": stream}
        logger.debug(f"Enviando requisição POST para {self.api_url}")
        try:
            response = requests.post(
                self.api_url,
                json=payload_with_stream,
                timeout=self.timeout,
                stream=stream,
            )
            response.raise_for_status()
            return response
        except Timeout as exc:
            raise ModelTimeoutError(str(exc)) from exc
        except HTTPError as exc:
            # Preserva o objeto HTTP na cadeia para o fallback de compatibilidade.
            raise ModelConnectionError(str(exc), response=exc.response) from exc
        except RequestException as exc:
            raise ModelConnectionError(str(exc)) from exc

    @staticmethod
    def parse_response(data: Any) -> ModelResponse:
        try:
            choice = data["choices"][0]
            message = choice["message"]
            content = message.get("content") or ""
            reasoning = message.get("reasoning_content") or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise ModelResponseError("Resposta do servidor em formato inesperado.") from exc
        usage_raw = data.get("usage") or {}
        usage = TokenUsage(
            input_tokens=int(usage_raw.get("prompt_tokens", 0) or 0),
            output_tokens=int(usage_raw.get("completion_tokens", 0) or 0),
            total_tokens=int(usage_raw.get("total_tokens", 0) or 0),
        )
        return ModelResponse(
            content=str(content),
            reasoning=str(reasoning),
            usage=usage,
            finish_reason=choice.get("finish_reason"),
            provider_metadata={"timings": data.get("timings")},
        )

    def complete_payload(self, payload: Dict[str, Any]) -> str:
        response = self.send_payload(payload, stream=False)
        try:
            data = response.json()
        except ValueError as exc:
            raise ModelResponseError("Resposta do servidor não contém JSON válido.") from exc
        return self.parse_response(data).content

    def complete(self, request: ModelRequest) -> ModelResponse:
        response = self.send_payload(self.build_payload(request), stream=False)
        try:
            data = response.json()
        except ValueError as exc:
            raise ModelResponseError("Resposta do servidor não contém JSON válido.") from exc
        return self.parse_response(data)

    @staticmethod
    def _decode_stream_line(line: bytes) -> tuple[Dict[str, Any] | None, bool]:
        line_str = line.decode("utf-8")
        if line_str.startswith("data: "):
            line_str = line_str[6:]
        if line_str.strip() == "[DONE]":
            return None, True
        try:
            data = json.loads(line_str)
        except json.JSONDecodeError:
            return None, False
        return data if isinstance(data, dict) else None, False

    @staticmethod
    def _events_from_stream_data(data: Dict[str, Any]) -> list[StreamEvent]:
        if "error" in data:
            raw_error = data["error"]
            message = raw_error.get("message", str(raw_error)) if isinstance(raw_error, dict) else str(raw_error)
            return [StreamEvent(StreamEventType.ERROR, text=message)]
        choices = data.get("choices")
        if not choices:
            return []
        delta = choices[0].get("delta", {})
        events: list[StreamEvent] = []
        if delta.get("reasoning_content"):
            events.append(StreamEvent(StreamEventType.REASONING, text=str(delta["reasoning_content"])))
        if delta.get("content"):
            events.append(StreamEvent(StreamEventType.CONTENT, text=str(delta["content"])))
        return events

    @classmethod
    def iter_stream(cls, response: Response) -> Iterator[StreamEvent]:
        last_timings: Optional[Dict[str, Any]] = None
        for line in response.iter_lines():
            if not line:
                continue
            data, done = cls._decode_stream_line(line)
            if done:
                break
            if data is None:
                continue
            if isinstance(data.get("timings"), dict):
                last_timings = data["timings"]
            for event in cls._events_from_stream_data(data):
                yield event
                if event.type == StreamEventType.ERROR:
                    return
        yield StreamEvent(StreamEventType.DONE, data=last_timings or {})

    def stream(self, request: ModelRequest) -> Iterator[StreamEvent]:
        if not self.capabilities.streaming:
            raise UnsupportedModelCapability("O provider não suporta streaming.")
        response = self.send_payload(self.build_payload(request), stream=True)
        yield from self.iter_stream(response)

    def consume_stream(self, response: Response, callbacks: Dict[str, Callable[..., Any]]) -> str:
        visible = ""
        raw_callback = callbacks.get("on_raw_line")
        # O adapter normalizado não expõe linhas brutas; mantém callback por
        # compatibilidade com valor vazio em vez de fazer o core interpretar SSE.
        if raw_callback:
            raw_callback("")
        for event in self.iter_stream(response):
            if event.type == StreamEventType.REASONING and callbacks.get("on_thinking_chunk"):
                callbacks["on_thinking_chunk"](event.text)
            elif event.type == StreamEventType.CONTENT:
                if callbacks.get("on_content_chunk"):
                    callbacks["on_content_chunk"](event.text)
                visible += event.text
            elif event.type == StreamEventType.ERROR:
                if callbacks.get("on_error"):
                    callbacks["on_error"](event.text)
                return ""
            elif event.type == StreamEventType.DONE and callbacks.get("on_done") and event.data:
                callbacks["on_done"](event.data)
        return visible.strip()

    def count_tokens(self, text: str) -> Optional[int]:
        if not self.capabilities.token_counting:
            return None
        tokenize_path = str(self.provider_options.get("tokenize_path", "/tokenize"))
        base_url = self.api_url.rsplit("/v1/", 1)[0]
        tokenize_url = f"{base_url}{tokenize_path}"
        try:
            response = requests.post(tokenize_url, json={"content": text}, timeout=min(self.timeout, 10))
            if response.status_code != 200:
                return None
            tokens = response.json().get("tokens", [])
            return len(tokens) if isinstance(tokens, list) else None
        except RequestException:
            return None

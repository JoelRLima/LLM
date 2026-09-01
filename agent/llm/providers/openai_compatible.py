"""Adapter para endpoints OpenAI-compatible de Chat Completions.

Todos os detalhes de HTTP, `choices`, SSE, GBNF, `chat_template_kwargs` e
`/tokenize` ficam confinados neste módulo.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Iterator, Optional

import requests
from requests import Response
from requests.exceptions import HTTPError, RequestException, Timeout

from agent.llm.contracts import (
    ModelRequest,
    ModelResponse,
    ProviderCapabilities,
    StreamEvent,
    StreamEventType,
    StructuredOutputMode,
    TokenUsage,
)
from agent.llm.errors import (
    ModelConnectionError,
    ModelResponseError,
    ModelTimeoutError,
    UnsupportedModelCapability,
)
from agent.llm.model_profile import ResolvedModelProfile, resolve_model_profile
from agent.llm.model_profile_compat import thaw_provider_options
from agent.llm.providers.openai_input_tokens import (
    extension_url,
    measure_request_input_tokens,
)
from agent.runtime.budget_estimation import RequestInputMeasurement
from agent.runtime.logging import logger


def _token_value(data: Dict[str, Any], primary: str, legacy: str) -> int | None:
    for key in (primary, legacy):
        if key not in data:
            continue
        value = data[key]
        if value is None or isinstance(value, bool):
            return None
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed >= 0 else None
    return None


class OpenAICompatibleGateway:
    provider_name = "openai_compatible"

    def __init__(self, profile: ResolvedModelProfile | Dict[str, Any]) -> None:
        self.resolved_profile = (
            profile if isinstance(profile, ResolvedModelProfile) else resolve_model_profile(profile)
        )
        self.profile = self.resolved_profile.to_dict()
        self.model = self.resolved_profile.model
        self.timeout = self.resolved_profile.timeout
        self.provider_options = thaw_provider_options(self.resolved_profile.provider_options)
        self.api_url = self.resolved_profile.api_url
        self.endpoint_identity = self.resolved_profile.endpoint_identity
        self._capabilities = self.resolved_profile.capabilities

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
        payload.update({"stream_options": {**dict(payload.get("stream_options") or {}), "include_usage": True}} if request.stream else {})
        return payload

    def _send_payload(self, payload: Dict[str, Any], stream: bool) -> Response:
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
            # Preserve the HTTP response for provider-error classification.
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
        usage_raw = data.get("usage")
        if isinstance(usage_raw, dict):
            usage = TokenUsage(
                input_tokens=_token_value(usage_raw, "input_tokens", "prompt_tokens"),
                output_tokens=_token_value(usage_raw, "output_tokens", "completion_tokens"),
                total_tokens=_token_value(usage_raw, "total_tokens", "total_tokens"),
                available=True,
            )
        else:
            usage = TokenUsage(available=False)
        return ModelResponse(
            content=str(content),
            reasoning=str(reasoning),
            usage=usage,
            finish_reason=choice.get("finish_reason"),
            provider_metadata={
                "timings": data.get("timings"),
                "observed_provider_model_id": data.get("model"),
            },
        )

    def complete(self, request: ModelRequest) -> ModelResponse:
        response = self._send_payload(self.build_payload(request), stream=False)
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
        events: list[StreamEvent] = []
        usage = data.get("usage")
        if isinstance(usage, dict):
            events.append(StreamEvent(StreamEventType.USAGE, data=usage))
        if "error" in data:
            raw_error = data["error"]
            message = raw_error.get("message", str(raw_error)) if isinstance(raw_error, dict) else str(raw_error)
            return [StreamEvent(StreamEventType.ERROR, text=message)]
        choices = data.get("choices")
        if not choices:
            return events
        delta = choices[0].get("delta", {})
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
        response = self._send_payload(self.build_payload(request), stream=True)
        yield from self.iter_stream(response)

    def measure_request_input_tokens(self, request: ModelRequest) -> RequestInputMeasurement:
        return measure_request_input_tokens(self, request)

    def count_tokens(self, text: str) -> Optional[int]:
        """Return a lower-fidelity text count, never exact chat-request usage."""

        if not self.capabilities.token_counting:
            return None
        tokenize_path = str(self.provider_options.get("tokenize_path", "/tokenize"))
        tokenize_url = extension_url(self.api_url, tokenize_path)
        try:
            response = requests.post(tokenize_url, json={"content": text}, timeout=min(self.timeout, 10))
            if response.status_code != 200:
                return None
            data = response.json()
            tokens = data.get("tokens", []) if isinstance(data, dict) else []
            return len(tokens) if isinstance(tokens, list) else None
        except (RequestException, TypeError, ValueError):
            return None

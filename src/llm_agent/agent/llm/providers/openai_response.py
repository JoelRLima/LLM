"""Validate provider response shapes before exposing neutral model values."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from llm_agent.agent.llm.contracts import ModelResponse, StreamEvent, StreamEventType, TokenUsage
from llm_agent.agent.llm.errors import ModelResponseError


@dataclass
class StreamCompletion:
    """Require a provider terminal marker instead of treating EOF as success."""

    marked_done: bool = False
    finish_reason: str | None = None

    def observe(self, data: dict[str, Any]) -> None:
        if data.get("choices") in (None, []):
            return
        reason = _choice(data).get("finish_reason")
        if reason is not None:
            if not isinstance(reason, str) or not reason:
                raise ModelResponseError("Stream do servidor contém finish_reason inválido.")
            self.finish_reason = reason

    def error_message(self) -> str | None:
        if self.finish_reason == "length":
            return "A resposta do modelo foi interrompida pelo limite de saída."
        if self.finish_reason == "content_filter":
            return "A resposta do modelo foi interrompida pelo provedor."
        if not self.marked_done and self.finish_reason is None:
            return "A conexão terminou sem confirmar a conclusão da resposta do modelo."
        return None

    def final_event(self, timings: dict[str, Any] | None) -> StreamEvent:
        error = self.error_message()
        return (StreamEvent(StreamEventType.ERROR, text=error) if error else
                StreamEvent(StreamEventType.DONE, data=timings or {}))


def _text(value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ModelResponseError("Resposta do servidor contém texto em formato inesperado.")
    return value


def _choice(data: dict[str, Any]) -> dict[str, Any]:
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise ModelResponseError("Resposta do servidor contém choices em formato inesperado.")
    return choices[0]


def _tokens(data: dict[str, Any], primary: str, legacy: str) -> int | None:
    value = data.get(primary, data.get(legacy))
    if isinstance(value, str):
        value = value.strip()
        if not value.isascii() or not value.isdecimal():
            return None
        try:
            value = int(value)
        except ValueError:
            return None
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def parse_chat_response(data: Any) -> ModelResponse:
    """Reject malformed JSON shapes without coercing objects into answer text."""
    if not isinstance(data, dict):
        raise ModelResponseError("Resposta do servidor em formato inesperado.")
    choice = _choice(data)
    message = choice.get("message")
    if not isinstance(message, dict):
        raise ModelResponseError("Resposta do servidor contém message em formato inesperado.")
    finish_reason = choice.get("finish_reason")
    if finish_reason is not None and not isinstance(finish_reason, str):
        raise ModelResponseError("Resposta do servidor contém finish_reason inválido.")
    raw = data.get("usage")
    usage = TokenUsage(
        input_tokens=_tokens(raw, "input_tokens", "prompt_tokens"),
        output_tokens=_tokens(raw, "output_tokens", "completion_tokens"),
        total_tokens=_tokens(raw, "total_tokens", "total_tokens"),
    ) if isinstance(raw, dict) else TokenUsage(available=False)
    return ModelResponse(
        content=_text(message.get("content")), reasoning=_text(message.get("reasoning_content")),
        usage=usage, finish_reason=finish_reason,
        provider_metadata={
            "timings": data.get("timings"), "observed_provider_model_id": data.get("model"),
        },
    )


def decode_stream_line(line: bytes | str) -> tuple[dict[str, Any] | None, bool]:
    """Decode a provider data line, retaining compatibility with keepalive lines."""
    try:
        value = line.decode("utf-8") if isinstance(line, bytes) else line
    except UnicodeDecodeError as exc:
        raise ModelResponseError("Stream do servidor contém UTF-8 inválido.") from exc
    data_line = value.startswith("data:")
    if data_line:
        value = value[5:].lstrip(" ")
    if value.strip() == "[DONE]":
        return None, True
    try:
        data = json.loads(value)
    except json.JSONDecodeError as exc:
        if data_line:
            raise ModelResponseError("Stream do servidor contém JSON inválido.") from exc
        return None, False
    if data_line and not isinstance(data, dict):
        raise ModelResponseError("Stream do servidor contém dados em formato inesperado.")
    return data if isinstance(data, dict) else None, False


def stream_data_events(data: dict[str, Any]) -> list[StreamEvent]:
    """Keep protocol failures distinct from content and normal completion."""
    if "error" in data:
        error = data["error"]
        message = error.get("message", "Provider stream error") if isinstance(error, dict) else str(error)
        return [StreamEvent(StreamEventType.ERROR, text=str(message))]
    events = []
    if isinstance(data.get("usage"), dict):
        events.append(StreamEvent(StreamEventType.USAGE, data=data["usage"]))
    if data.get("choices") in (None, []):
        return events
    delta = _choice(data).get("delta")
    if delta is None:
        return events
    if not isinstance(delta, dict):
        raise ModelResponseError("Stream do servidor contém delta em formato inesperado.")
    for name, event_type in (("reasoning_content", StreamEventType.REASONING), ("content", StreamEventType.CONTENT)):
        text = _text(delta.get(name))
        if text:
            events.append(StreamEvent(event_type, text=text))
    return events

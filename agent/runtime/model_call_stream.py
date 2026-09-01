"""Provider stream adapters used only by ModelCallService."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any

from agent.llm.contracts import StreamEventType
from agent.llm.errors import ModelResponseError


def _callback(callbacks: Mapping[str, Callable[..., Any]], name: str, value: Any) -> None:
    handler = callbacks.get(name)
    if handler is not None:
        handler(value)


def observed_stream_response(visible: str, usage: Any) -> Any:
    """Keep visible output available for fallback while preserving usage."""

    if usage is None:
        return visible
    return {"content": visible, "usage": usage}


def consume_events(
    events: Iterable[Any], callbacks: Mapping[str, Callable[..., Any]]
) -> tuple[str, Any]:
    raw_callback = callbacks.get("on_raw_line")
    if raw_callback is not None:
        raw_callback("")
    visible = ""
    usage: Any = None
    for event in events:
        event_type = getattr(event, "type", None)
        event_value = getattr(event_type, "value", event_type)
        if event_type is StreamEventType.REASONING or event_value == StreamEventType.REASONING.value:
            _callback(callbacks, "on_thinking_chunk", getattr(event, "text", ""))
        elif event_type is StreamEventType.CONTENT or event_value == StreamEventType.CONTENT.value:
            text = str(getattr(event, "text", ""))
            visible += text
            _callback(callbacks, "on_content_chunk", text)
        elif event_type is StreamEventType.USAGE or event_value == StreamEventType.USAGE.value:
            usage = getattr(event, "data", None)
            _callback(callbacks, "on_usage", usage)
        elif event_type is StreamEventType.ERROR or event_value == StreamEventType.ERROR.value:
            error_text = str(getattr(event, "text", ""))
            _callback(callbacks, "on_error", error_text)
            error = ModelResponseError(error_text, partial_content=visible)
            error.stream_usage = usage  # type: ignore[attr-defined]
            raise error
        elif event_type is StreamEventType.DONE or event_value == StreamEventType.DONE.value:
            data = getattr(event, "data", None)
            if data:
                _callback(callbacks, "on_done", data)
    return visible, usage


__all__ = [
    "consume_events",
    "observed_stream_response",
]

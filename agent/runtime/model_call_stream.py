"""Provider stream adapters used only by ModelCallService."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any, cast

from agent.llm.contracts import ModelRequest, ModelResponseError, StreamEventType
from agent.llm.legacy_payload import legacy_payload


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


def native_events(gateway: Any, request: ModelRequest) -> Iterable[Any]:
    return cast(Iterable[Any], gateway.stream(request))


def consume_legacy_response(
    gateway: Any,
    response: Any,
    callbacks: Mapping[str, Callable[..., Any]],
) -> tuple[str, Any]:
    usage: Any = None

    def capture_usage(value: Any) -> None:
        nonlocal usage
        usage = value
        _callback(callbacks, "on_usage", value)

    stream_callbacks = dict(callbacks)
    stream_callbacks["on_usage"] = capture_usage
    try:
        visible = cast(str, gateway.consume_stream(response, stream_callbacks))
    except BaseException as exc:
        exc.stream_usage = usage  # type: ignore[attr-defined]
        raise
    return visible, usage


def start_legacy_stream(gateway: Any, request: ModelRequest) -> Any:
    payload = legacy_payload(gateway, request)
    return gateway.send_payload(payload, stream=True)


def consume_legacy_request(
    gateway: Any,
    request: ModelRequest,
    callbacks: Mapping[str, Callable[..., Any]],
) -> tuple[str, Any]:
    return consume_legacy_response(
        gateway,
        start_legacy_stream(gateway, request),
        callbacks,
    )


__all__ = [
    "consume_events",
    "consume_legacy_response",
    "consume_legacy_request",
    "native_events",
    "observed_stream_response",
    "start_legacy_stream",
]

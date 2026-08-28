"""Canonical pre-dispatch request-input measurement and fallbacks."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any, Callable

PROVIDER_CHAT_INPUT_TOKENS = "provider_chat_input_tokens"
PROVIDER_TEXT_TOKENIZER = "provider_text_tokenizer"
HEURISTIC_CHARS_PER_TOKEN = "heuristic_chars_per_token"
UNAVAILABLE = "unavailable"


def _non_negative_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


@dataclass(frozen=True, slots=True)
class RequestInputMeasurement:
    """One bounded pre-dispatch input measurement for a canonical request."""

    token_count: int | None
    source: str = UNAVAILABLE
    exact: bool = False
    available: bool = False

    def __post_init__(self) -> None:
        count = self.token_count
        if count is not None and _non_negative_int(count) is None:
            raise ValueError("token_count must be a non-negative integer or None")
        if self.available and count is None:
            raise ValueError("an available measurement must contain a token count")
        if self.exact and self.source != PROVIDER_CHAT_INPUT_TOKENS:
            raise ValueError("only provider chat input tokens can be exact")
        if self.source == PROVIDER_CHAT_INPUT_TOKENS and not self.exact:
            raise ValueError("provider chat input tokens must be exact")
        if not self.source:
            raise ValueError("measurement source is required")

    @property
    def tokens(self) -> int | None:
        return self.token_count

    @property
    def input_tokens(self) -> int | None:
        return self.token_count

    def __iter__(self) -> Iterator[int | str]:
        """Allow old ``count, source = ...`` callers to migrate safely."""

        yield self.token_count if self.token_count is not None else 0
        yield self.source

    @classmethod
    def unavailable(cls) -> "RequestInputMeasurement":
        return cls(None, UNAVAILABLE, exact=False, available=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_input_tokens": self.token_count,
            "request_input_measurement_source": self.source,
            "request_input_measurement_exact": self.exact,
            "request_input_measurement_available": self.available,
        }


def _measurement(
    token_count: Any,
    source: str,
    *,
    exact: bool,
    available: bool = True,
) -> RequestInputMeasurement | None:
    count = _non_negative_int(token_count)
    if count is None:
        return None
    return RequestInputMeasurement(count, source, exact=exact, available=available)


def _available_count(measurement: RequestInputMeasurement) -> int:
    if not measurement.available or measurement.token_count is None:
        return 0
    return measurement.token_count


def _request_text(request: Any) -> str | None:
    if request is None:
        return None
    messages = getattr(request, "messages", None)
    if messages is None:
        return None
    try:
        values = [str(getattr(message, "content", "")) for message in messages]
    except TypeError:
        return None
    return "\n".join(values)


def _payload_text(payload: Any) -> str:
    messages = payload.get("messages") if isinstance(payload, Mapping) else None
    if not isinstance(messages, list):
        return ""
    return "\n".join(
        str(message.get("content", ""))
        for message in messages
        if isinstance(message, Mapping)
    )


def _conservative_text_tokens(text: str) -> int:
    """Conservative text estimate; it is never provider-reported truth."""

    if not text:
        return 0
    return max(1, (len(text) + 3) // 4)


def _provider_text_measurement(
    text: str,
    token_counter: Callable[[str], Any] | None,
) -> RequestInputMeasurement:
    if token_counter is not None:
        try:
            value = token_counter(text)
        except Exception as exc:
            from agent.runtime.budget import BudgetExhausted

            if isinstance(exc, BudgetExhausted):
                raise
            value = None
        measured = _measurement(value, PROVIDER_TEXT_TOKENIZER, exact=False)
        if measured is not None:
            return measured
    return RequestInputMeasurement(
        _conservative_text_tokens(text),
        HEURISTIC_CHARS_PER_TOKEN,
        exact=False,
        available=True,
    )


def _gateway_text_token_counter(gateway: Any) -> Callable[[str], Any] | None:
    """Return a declared provider text tokenizer, never a hidden heuristic."""

    candidate = getattr(gateway, "count_tokens", None)
    if not callable(candidate):
        return None
    capabilities = getattr(gateway, "capabilities", None)
    declared = (
        capabilities.get("token_counting")
        if isinstance(capabilities, Mapping)
        else getattr(capabilities, "token_counting", None)
    )
    # Legacy adapters without capabilities are allowed to expose a text
    # counter; explicit false wins because offline adapters commonly use chars/4.
    return None if declared is False else candidate


def _coerce_gateway_measurement(value: Any) -> RequestInputMeasurement | None:
    if isinstance(value, RequestInputMeasurement):
        return value if value.available and value.token_count is not None else None
    if isinstance(value, Mapping):
        count = value.get("request_input_tokens", value.get("token_count"))
        source = str(
            value.get(
                "request_input_measurement_source", value.get("source", UNAVAILABLE)
            )
        )
        if source == "provider_token_counter":
            source = PROVIDER_TEXT_TOKENIZER
        exact = bool(
            value.get("request_input_measurement_exact", value.get("exact", False))
        )
        if source == PROVIDER_CHAT_INPUT_TOKENS:
            exact = True
        return _measurement(count, source, exact=exact)
    if isinstance(value, tuple) and len(value) == 2:
        count, source = value
        normalized_source = (
            PROVIDER_TEXT_TOKENIZER
            if str(source) == "provider_token_counter"
            else str(source)
        )
        return _measurement(
            count,
            normalized_source,
            exact=normalized_source == PROVIDER_CHAT_INPUT_TOKENS,
        )
    return _measurement(value, PROVIDER_TEXT_TOKENIZER, exact=False)


def measure_request_input_tokens_from_texts(
    texts: list[str], token_counter: Callable[[str], Any] | None,
) -> RequestInputMeasurement:
    """Measure a text fallback with provenance; never call it exact."""

    return _provider_text_measurement("\n".join(texts), token_counter)


def measure_model_request_input_tokens(
    request: Any,
    gateway: Any = None,
    *,
    token_counter: Callable[[str], Any] | None = None,
) -> RequestInputMeasurement:
    """Measure one exact ``ModelRequest`` before provider dispatch."""

    if request is None:
        return RequestInputMeasurement.unavailable()
    if (
        token_counter is None
        and callable(gateway)
        and not callable(getattr(gateway, "measure_request_input_tokens", None))
    ):
        token_counter, gateway = gateway, None
    measure = getattr(gateway, "measure_request_input_tokens", None)
    if callable(measure):
        try:
            provider_measurement = _coerce_gateway_measurement(measure(request))
        except Exception as exc:
            from agent.runtime.budget import BudgetExhausted

            if isinstance(exc, BudgetExhausted):
                raise
            provider_measurement = None
        if provider_measurement is not None:
            return provider_measurement
    if token_counter is None:
        token_counter = _gateway_text_token_counter(gateway)
    text = _request_text(request)
    if text is None:
        return RequestInputMeasurement.unavailable()
    return _provider_text_measurement(text, token_counter)


def measure_payload_input_tokens(
    payload: Any,
    token_counter: Callable[[str], Any] | None = None,
) -> RequestInputMeasurement:
    """Measure a legacy payload only as explicit text/fallback provenance."""

    return _provider_text_measurement(_payload_text(payload), token_counter)


__all__ = [
    "HEURISTIC_CHARS_PER_TOKEN",
    "PROVIDER_CHAT_INPUT_TOKENS",
    "PROVIDER_TEXT_TOKENIZER",
    "RequestInputMeasurement",
    "UNAVAILABLE",
    "measure_model_request_input_tokens",
    "measure_payload_input_tokens",
    "measure_request_input_tokens_from_texts",
]

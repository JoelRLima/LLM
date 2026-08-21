"""Compatibility methods for the pre-ModelRequest ChatSession API."""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional, cast

from agent.llm.contracts import ModelRequest


class LegacySessionMixin:
    """Keep payload transport available for old adapters and persisted callers."""

    def build_payload(
        self: Any,
        response_format: Optional[str] = None,
        grammar: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Legacy payload facade; canonical callers use ``build_request``."""

        request = self.build_request(response_format, grammar)
        from agent.llm.legacy_payload import legacy_payload

        return legacy_payload(self.gateway, request)

    def build_legacy_request(
        self: Any,
        payload: Dict[str, Any],
        *,
        grammar: Optional[str] = None,
    ) -> ModelRequest:
        from agent.llm.session_requests import build_legacy_model_request

        return build_legacy_model_request(self, payload, grammar=grammar)

    def send_request(self: Any, payload: Dict[str, Any], stream: bool = True) -> Any:
        """Fachada legada que preserva o transporte raw e o envelope de stream."""
        from agent.llm.session_stream_legacy import send_legacy_request

        return send_legacy_request(self, payload, stream=stream)

    def send_non_streaming_request(self: Any, payload: Dict[str, Any]) -> str:
        """Envia sem streaming pelo boundary canônico e retorna somente o texto."""
        response = self.complete_request(self.build_legacy_request(payload))
        return cast(str, response.content)

    def process_stream(
        self: Any, response: Any, callbacks: Dict[str, Callable]
    ) -> str:
        """Consome o stream e encaminha chunks aos callbacks fornecidos."""
        from agent.llm.session_stream_legacy import process_legacy_stream

        return process_legacy_stream(self, response, callbacks)

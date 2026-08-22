"""Transparent evaluation-only model trace recorder."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any


class RecordingGateway:
    """Observe model calls while preserving the wrapped gateway contract.

    The recorder does not retry, edit requests/responses, alter budgets, or
    decide outcomes.  It records bounded metadata plus the raw response text;
    the Block 7 evidence serializer performs sanitization when exporting it.
    """

    def __init__(self, gateway: Any) -> None:
        self.gateway = gateway
        self._records: list[dict[str, Any]] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(self.gateway, name)

    @staticmethod
    def _stage(request: Any) -> str:
        messages = getattr(request, "messages", ())
        prompt = str(getattr(messages[-1], "content", "")) if messages else ""
        system = str(getattr(messages[0], "content", "")) if messages else ""
        system_lowered = system.casefold()
        prompt_lowered = prompt.casefold()
        if "router agent" in system_lowered:
            return "route"
        if any(token in prompt_lowered for token in ("reparo", "repair", "corrija", "proposta de código")):
            return "repair"
        if any(token in prompt_lowered for token in ("continu", "observação insuficiente", "observation")):
            return "continuation"
        return "decision"

    def _request_summary(self, request: Any) -> dict[str, Any]:
        structured = getattr(request, "structured_output", None)
        return {
            "model": str(getattr(request, "model", "")),
            "temperature": getattr(request, "temperature", None),
            "max_output_tokens": getattr(request, "max_output_tokens", None),
            "stream": bool(getattr(request, "stream", False)),
            "reasoning_budget": getattr(request, "reasoning_budget", None),
            "structured_mode": str(getattr(structured, "mode", "")) if structured is not None else None,
            "message_count": len(getattr(request, "messages", ()) or ()),
        }

    def complete(self, request: Any) -> Any:
        record: dict[str, Any] = {
            "call_index": len(self._records) + 1,
            "stage": self._stage(request),
            "request": self._request_summary(request),
        }
        try:
            response = self.gateway.complete(request)
        except Exception as exc:
            record["error"] = f"{type(exc).__name__}: {exc}"
            self._records.append(record)
            raise
        record["response"] = str(getattr(response, "content", response))
        record["reasoning"] = str(getattr(response, "reasoning", ""))
        record["finish_reason"] = getattr(response, "finish_reason", None)
        usage = getattr(response, "usage", None)
        if usage is not None:
            record["usage"] = {
                "input_tokens": getattr(usage, "input_tokens", None),
                "output_tokens": getattr(usage, "output_tokens", None),
                "total_tokens": getattr(usage, "total_tokens", None),
                "available": getattr(usage, "available", False),
            }
        provider_metadata = getattr(response, "provider_metadata", None)
        if isinstance(provider_metadata, dict):
            record["provider_metadata"] = dict(provider_metadata)
        self._records.append(record)
        return response

    def stream(self, request: Any) -> Iterator[Any]:
        record: dict[str, Any] = {
            "call_index": len(self._records) + 1,
            "stage": self._stage(request),
            "request": self._request_summary(request),
        }
        chunks: list[str] = []
        try:
            for event in self.gateway.stream(request):
                text = getattr(event, "text", "")
                if text:
                    chunks.append(str(text))
                yield event
        except Exception as exc:
            record["error"] = f"{type(exc).__name__}: {exc}"
            record["response"] = "".join(chunks)
            self._records.append(record)
            raise
        record["response"] = "".join(chunks)
        self._records.append(record)

    def count_tokens(self, text: str) -> Any:
        return self.gateway.count_tokens(text)

    def export_evidence(self) -> dict[str, Any]:
        records = [dict(record) for record in self._records]
        provider = getattr(self.gateway, "provider_name", None)
        model = getattr(self.gateway, "model", None)
        profile = getattr(self.gateway, "profile", None)
        capabilities = getattr(self.gateway, "capabilities", None)
        capability_projection = {
            "streaming": bool(getattr(capabilities, "streaming", False)),
            "structured_output_modes": [
                str(getattr(mode, "value", mode))
                for mode in getattr(capabilities, "structured_output_modes", ())
            ],
            "reasoning": bool(getattr(capabilities, "reasoning", False)),
            "token_counting": bool(getattr(capabilities, "token_counting", False)),
            "tool_calls": bool(getattr(capabilities, "tool_calls", False)),
        }
        return {
            "model_decisions": [record for record in records if record.get("stage") == "decision"],
            "repair_decisions": [record for record in records if record.get("stage") == "repair"],
            "route_decisions": [record for record in records if record.get("stage") in {"route", "continuation"}],
            "model_calls": records,
            "provider_identity": {
                "provider": str(provider or ""),
                "model": str(model or ""),
                "profile": dict(profile) if isinstance(profile, dict) else {},
                "capabilities": capability_projection,
                "endpoint_identity": getattr(self.gateway, "endpoint_identity", None),
                "actual_provider_model_id": getattr(self.gateway, "provider_model_id", None),
            },
        }


__all__ = ["RecordingGateway"]

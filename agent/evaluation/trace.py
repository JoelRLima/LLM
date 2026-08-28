"""Transparent evaluation-only model trace recorder."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Iterator, Mapping
from typing import Any

from agent.llm.contracts import StreamEventType, normalize_usage
from agent.llm.decision_contract import request_contract_value
from agent.llm.identity import (
    call_identity,
    declared_provider_identity,
    normalize_external_identity,
    observed_provider_model_id,
    project_observed_provider_identity,
)
from agent.runtime.budget_estimation import (
    RequestInputMeasurement,
    measure_model_request_input_tokens,
)


class RecordingGateway:
    """Observe model calls without changing provider behavior or outcomes."""

    def __init__(self, gateway: Any, *, external_identity: str | None = None) -> None:
        self.gateway = gateway
        configured_external_identity = (
            external_identity
            if external_identity is not None
            else getattr(gateway, "external_identity", None)
        )
        self.external_identity = normalize_external_identity(configured_external_identity)
        self._records: list[dict[str, Any]] = []
        self._pending_request_measurements: dict[int, RequestInputMeasurement] = {}

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
        raw_structured_mode = getattr(structured, "mode", "") if structured is not None else None
        structured_mode = (
            str(getattr(raw_structured_mode, "value", raw_structured_mode))
            if structured is not None
            else None
        )
        structured_contract_present = bool(
            structured is not None
            and (
                getattr(structured, "schema", None) is not None
                or getattr(structured, "grammar", None) not in (None, "")
                or getattr(structured, "instruction", None) not in (None, "")
                or structured_mode not in (None, "", "StructuredOutputMode.NONE", "none")
            )
        )
        request_input_measurement = self._pending_request_measurements.pop(
            id(request), None
        )
        if request_input_measurement is None:
            request_input_measurement = measure_model_request_input_tokens(
                request, self.gateway
            )
        estimated_input_tokens = request_input_measurement.token_count or 0
        estimation_source = request_input_measurement.source
        summary = {
            "model": str(getattr(request, "model", "")),
            "temperature": getattr(request, "temperature", None),
            "max_output_tokens": getattr(request, "max_output_tokens", None),
            "stream": bool(getattr(request, "stream", False)),
            "reasoning_budget": getattr(request, "reasoning_budget", None),
            "request_contract": request_contract_value(
                getattr(request, "request_contract", None)
            ),
            "structured_mode": structured_mode,
            "structured_contract_present": structured_contract_present,
            "structured_contract": {
                "mode": structured_mode,
                "schema_present": getattr(structured, "schema", None) is not None,
                "grammar_present": getattr(structured, "grammar", None) not in (None, ""),
                "instruction_present": getattr(structured, "instruction", None) not in (None, ""),
            } if structured is not None else None,
            "message_count": len(getattr(request, "messages", ()) or ()),
            "request_input_tokens": request_input_measurement.token_count,
            "request_input_measurement_source": estimation_source,
            "request_input_measurement_exact": request_input_measurement.exact,
            "request_input_measurement_available": request_input_measurement.available,
            "estimated_request_tokens": estimated_input_tokens,
            "request_estimation_source": estimation_source,
            "context_compacted": bool(getattr(request, "context_compacted", False)),
            "context_limit": getattr(request, "context_limit", None),
        }
        context_limit = summary["context_limit"]
        summary["request_utilization_ratio"] = (
            estimated_input_tokens / context_limit
            if isinstance(context_limit, int) and not isinstance(context_limit, bool) and context_limit > 0
            else None
        )
        stable_config = {
            key: summary[key]
            for key in (
                "model",
                "temperature",
                "max_output_tokens",
                "stream",
                "reasoning_budget",
                "request_contract",
                "structured_mode",
                "structured_contract",
            )
        }
        fingerprint_payload = json.dumps(stable_config, sort_keys=True, separators=(",", ":"), default=str)
        summary["config_fingerprint"] = hashlib.sha256(
            fingerprint_payload.encode("utf-8")
        ).hexdigest()
        return summary

    def measure_request_input_tokens(
        self, request: Any
    ) -> RequestInputMeasurement:
        """Carry the session's one request measurement into trace evidence."""

        measurement = measure_model_request_input_tokens(request, self.gateway)
        self._pending_request_measurements[id(request)] = measurement
        return measurement

    @staticmethod
    def _apply_input_reconciliation(
        record: dict[str, Any], input_tokens: int | None
    ) -> None:
        request = record.get("request")
        measured = request.get("request_input_tokens") if isinstance(request, Mapping) else None
        if (
            isinstance(input_tokens, int)
            and not isinstance(input_tokens, bool)
            and input_tokens >= 0
            and isinstance(measured, int)
            and not isinstance(measured, bool)
            and measured >= 0
        ):
            delta = input_tokens - measured
            record["request_input_token_delta"] = delta
            record["request_input_token_abs_delta"] = abs(delta)
            record["request_input_token_consistent"] = delta == 0

    def _apply_response_identity(self, record: dict[str, Any], response: Any) -> None:
        observed = observed_provider_model_id(getattr(response, "provider_metadata", None))
        if observed is not None:
            record["observed_provider_model_id"] = observed
            record["identity_source"] = "response.provider_metadata"

    def _apply_stream_identity(self, record: dict[str, Any], event: Any) -> None:
        data = getattr(event, "data", None)
        metadata = data.get("provider_metadata") if isinstance(data, dict) else data
        observed = observed_provider_model_id(metadata)
        if observed is not None:
            record["observed_provider_model_id"] = observed
            record["identity_source"] = "stream.event_metadata"

    def complete(self, request: Any) -> Any:
        started_at = time.monotonic()
        record: dict[str, Any] = {
            "call_index": len(self._records) + 1,
            "stage": self._stage(request),
            "request": self._request_summary(request),
        }
        record.update(call_identity(self.gateway, request, len(self._records) + 1))
        try:
            response = self.gateway.complete(request)
        except Exception as exc:
            record["error"] = f"{type(exc).__name__}: {exc}"
            record["provider_call_succeeded"] = False
            record["duration_ms"] = max(0, int((time.monotonic() - started_at) * 1000))
            self._records.append(record)
            raise
        record["provider_call_succeeded"] = True
        record["duration_ms"] = max(0, int((time.monotonic() - started_at) * 1000))
        self._apply_response_identity(record, response)
        record["response"] = str(getattr(response, "content", response))
        record["finish_reason"] = getattr(response, "finish_reason", None)
        usage = getattr(response, "usage", None)
        if usage is not None:
            input_tokens, output_tokens, total_tokens, _, complete = normalize_usage(usage)
            usage_available = (
                usage.get("available", True)
                if isinstance(usage, Mapping)
                else getattr(usage, "available", False)
            )
            record["usage"] = {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": total_tokens,
                "available": usage_available,
                "complete": complete,
                "source": "provider_reported" if complete else "provider_incomplete",
            }
            self._apply_input_reconciliation(record, input_tokens)
        provider_metadata = getattr(response, "provider_metadata", None)
        if isinstance(provider_metadata, dict):
            record["provider_metadata"] = dict(provider_metadata)
        self._records.append(record)
        return response

    def stream(self, request: Any) -> Iterator[Any]:
        started_at = time.monotonic()
        record: dict[str, Any] = {
            "call_index": len(self._records) + 1,
            "stage": self._stage(request),
            "request": self._request_summary(request),
        }
        record.update(call_identity(self.gateway, request, len(self._records) + 1))
        chunks: list[str] = []
        observed_usage: Any = None
        try:
            for event in self.gateway.stream(request):
                self._apply_stream_identity(record, event)
                if getattr(event, "type", None) is StreamEventType.USAGE:
                    observed_usage = getattr(event, "data", None)
                text = getattr(event, "text", "")
                if getattr(event, "type", None) is StreamEventType.CONTENT and text:
                    chunks.append(str(text))
                yield event
        except Exception as exc:
            record["error"] = f"{type(exc).__name__}: {exc}"
            record["response"] = "".join(chunks)
            record["provider_call_succeeded"] = False
            self._record_stream_usage(record, observed_usage)
            record["duration_ms"] = max(0, int((time.monotonic() - started_at) * 1000))
            self._records.append(record)
            raise
        record["response"] = "".join(chunks)
        record["provider_call_succeeded"] = True
        self._record_stream_usage(record, observed_usage)
        record["duration_ms"] = max(0, int((time.monotonic() - started_at) * 1000))
        self._records.append(record)

    def _record_stream_usage(self, record: dict[str, Any], usage: Any) -> None:
        if usage is None:
            return
        input_tokens, output_tokens, total_tokens, _, complete = normalize_usage(usage)
        record["usage"] = {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
            "available": usage.get("available", True)
            if isinstance(usage, Mapping)
            else getattr(usage, "available", False),
            "complete": complete,
            "source": "provider_reported" if complete else "provider_incomplete",
        }
        self._apply_input_reconciliation(record, input_tokens)

    def count_tokens(self, text: str) -> Any:
        return self.gateway.count_tokens(text)

    def export_evidence(self) -> dict[str, Any]:
        records = [dict(record) for record in self._records]
        declared = declared_provider_identity(self.gateway)
        observed = project_observed_provider_identity(
            records,
            declared,
            external_identity=self.external_identity,
        )
        call_identities = observed["call_identities"]
        return {
            "model_decisions": [record for record in records if record.get("stage") == "decision"],
            "repair_decisions": [record for record in records if record.get("stage") == "repair"],
            "route_decisions": [record for record in records if record.get("stage") in {"route", "continuation"}],
            "model_calls": records,
            "model_call_identities": call_identities,
            "provider_identity": {
                **declared,
                "actual_provider_model_id": getattr(self.gateway, "provider_model_id", None),
            },
            "declared_provider_identity": declared,
            "observed_provider_identity": observed,
        }

__all__ = ["RecordingGateway"]

"""Transparent evaluation-only model trace recorder."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from agent.evaluation.block7_model_identity import (
    GENERIC_MODEL_ALIASES,
    normalize_endpoint_identity,
    normalize_external_identity,
)
from agent.evaluation.block7_trace_identity import (
    call_identity,
    observed_provider_model_id,
)


class RecordingGateway:
    """Observe model calls while preserving the wrapped gateway contract.

    The recorder does not retry, edit requests/responses, alter budgets, or
    decide outcomes.  It records bounded metadata plus the raw response text;
    the Block 7 evidence serializer performs sanitization when exporting it.
    """

    def __init__(self, gateway: Any, *, external_identity: str | None = None) -> None:
        self.gateway = gateway
        configured_external_identity = (
            external_identity
            if external_identity is not None
            else getattr(gateway, "external_identity", None)
        )
        self.external_identity = normalize_external_identity(configured_external_identity)
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
        return {
            "model": str(getattr(request, "model", "")),
            "temperature": getattr(request, "temperature", None),
            "max_output_tokens": getattr(request, "max_output_tokens", None),
            "stream": bool(getattr(request, "stream", False)),
            "reasoning_budget": getattr(request, "reasoning_budget", None),
            "structured_mode": structured_mode,
            "structured_contract_present": structured_contract_present,
            "structured_contract": {
                "mode": structured_mode,
                "schema_present": getattr(structured, "schema", None) is not None,
                "grammar_present": getattr(structured, "grammar", None) not in (None, ""),
                "instruction_present": getattr(structured, "instruction", None) not in (None, ""),
            } if structured is not None else None,
            "message_count": len(getattr(request, "messages", ()) or ()),
        }

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
            self._records.append(record)
            raise
        self._apply_response_identity(record, response)
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
        record.update(call_identity(self.gateway, request, len(self._records) + 1))
        chunks: list[str] = []
        try:
            for event in self.gateway.stream(request):
                self._apply_stream_identity(record, event)
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
        declared = {
            "provider": str(provider or ""),
            "model": str(model or ""),
            "profile": dict(profile) if isinstance(profile, dict) else {},
            "capabilities": capability_projection,
            "endpoint_identity": normalize_endpoint_identity(getattr(self.gateway, "endpoint_identity", None)),
        }
        call_identities = [
            {
                key: record.get(key)
                for key in (
                    "call_index",
                    "provider",
                    "endpoint_identity",
                    "declared_model",
                    "observed_provider_model_id",
                    "identity_source",
                )
            }
            for record in records
        ]
        observed_ids = [
            str(identity["observed_provider_model_id"])
            for identity in call_identities
            if identity.get("observed_provider_model_id") not in (None, "")
        ]
        distinct_observed_ids = list(dict.fromkeys(observed_ids))
        generic_aliases = GENERIC_MODEL_ALIASES
        specific = len(distinct_observed_ids) == 1 and distinct_observed_ids[0].casefold() not in generic_aliases
        external_identity = self.external_identity
        providers = list(dict.fromkeys(
            str(identity["provider"]).strip()
            for identity in call_identities
            if identity.get("provider") not in (None, "")
        ))
        endpoints = list(dict.fromkeys(
            str(identity["endpoint_identity"]).strip()
            for identity in call_identities
            if identity.get("endpoint_identity") not in (None, "")
        ))
        consistent = len(distinct_observed_ids) <= 1 and len(providers) <= 1 and len(endpoints) <= 1
        provider_observed = bool(distinct_observed_ids)
        complete = bool(call_identities) and all(
            all(key in identity for key in (
                "call_index",
                "provider",
                "endpoint_identity",
                "declared_model",
                "observed_provider_model_id",
                "identity_source",
            ))
            for identity in call_identities
        )
        sufficient = bool(complete and consistent and (specific or external_identity))
        observed_model_id = distinct_observed_ids[0] if len(distinct_observed_ids) == 1 else None
        provider_identity = providers[0] if len(providers) == 1 else None
        endpoint_identity = endpoints[0] if len(endpoints) == 1 else None
        observed_source = (
            "response.provider_metadata"
            if provider_observed and specific
            else "external_identity"
            if external_identity
            else "response.provider_metadata"
            if provider_observed
            else "unavailable"
        )
        observed = {
            "available": provider_observed or bool(external_identity),
            "provider_observation_available": provider_observed,
            "identity_sufficient": sufficient,
            "consistent": consistent,
            "specific": specific,
            "complete": complete,
            "provider_model_id": observed_model_id,
            "actual_provider_model_id": observed_model_id,
            "model": observed_model_id if provider_observed else None,
            "provider": provider_identity,
            "endpoint_identity": endpoint_identity,
            "source": observed_source,
            "identity_source": observed_source,
            "observed_model_ids": observed_ids,
            "distinct_observed_model_ids": distinct_observed_ids,
            "external_identity": external_identity,
            "external_identity_source": "external_identity" if external_identity else None,
            "provider_observation_limitation": (
                "generic_provider_model_id"
                if provider_observed and not specific
                else "backend_identity_unavailable"
                if not provider_observed
                else None
            ),
            "call_count": len(call_identities),
            "call_identities": call_identities,
        }
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

"""Typed model-call records and their existing publication boundary."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from agent.llm.model_metrics import build_model_call_metric
from agent.llm.model_profile import ResolvedModelProfile
from agent.runtime.budget_estimation import RequestInputMeasurement
from agent.runtime.logging import logger


@dataclass(frozen=True, slots=True)
class ModelCallRecord:
    """Immutable typed envelope around the canonical model-call projection."""

    fields: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "fields",
            MappingProxyType(deepcopy(dict(self.fields))),
        )

    def to_dict(self) -> dict[str, Any]:
        return deepcopy(dict(self.fields))

    @property
    def success(self) -> bool:
        return bool(self.fields.get("success", False))

    @property
    def call_number(self) -> int | None:
        value = self.fields.get("call_number")
        return value if isinstance(value, int) and not isinstance(value, bool) else None


@dataclass(frozen=True, slots=True)
class ModelCallOutcome:
    """Result of one provider transport attempt and its finalized record."""

    response: Any
    record: ModelCallRecord
    call_number: int
    usage: Any = None
    text: str = ""


class SessionMetricsSink:
    """Adapt the historical ChatSession callback to the context sink contract."""

    def __init__(self, session: Any) -> None:
        self._session = session

    def record(self, metric: dict[str, Any]) -> None:
        callback = getattr(self._session, "model_call_callback", None)
        if callable(callback):
            callback(metric)


def publish_record(service: Any, record: ModelCallRecord, operation: str) -> None:
    try:
        service.context.record_metric("model_call", record.to_dict())
    except Exception as exc:  # observability must not alter provider semantics
        logger.warning("Falha ao registrar chamada do modelo: %s", type(exc).__name__)
    try:
        service.context.emit(
            "model_call_completed",
            {
                "operation": operation,
                "provider": getattr(service.gateway, "provider_name", None),
                "call_number": record.call_number,
                "success": record.success,
            },
        )
    except Exception as exc:  # event observers are also non-authoritative
        logger.warning("Falha ao emitir evento de chamada do modelo: %s", type(exc).__name__)


def finalize_record(
    service: Any,
    *,
    request: Any,
    started_at: float,
    call_number: int,
    reserved_tokens: int,
    measurement: RequestInputMeasurement,
    estimated_tokens: int,
    success: bool,
    streaming: bool,
    response: Any,
    usage: Any,
    operation: str,
) -> ModelCallRecord:
    service.context.finalize_model_call(
        call_number,
        usage=usage,
        estimated_tokens=max(0, estimated_tokens),
    )
    profile = service.context.model_profile
    config = profile.to_dict() if isinstance(profile, ResolvedModelProfile) else service.context.metadata
    record = ModelCallRecord(
        build_model_call_metric(
            service.gateway,
            config,
            started_at,
            success=success,
            streaming=streaming,
            response=response,
            request=request,
            call_number=call_number,
            estimated_tokens=max(0, estimated_tokens),
            reserved_tokens=reserved_tokens,
            estimated_request_tokens=measurement.token_count or 0,
            request_estimation_source=measurement.source,
            context_limit=getattr(request, "context_limit", None),
            context_compacted=bool(getattr(request, "context_compacted", False)),
            request_input_measurement=measurement,
            operation=operation,
        )
    )
    publish_record(service, record, operation)
    return record


__all__ = [
    "ModelCallOutcome",
    "ModelCallRecord",
    "SessionMetricsSink",
    "finalize_record",
    "publish_record",
]

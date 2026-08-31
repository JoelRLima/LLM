"""Canonical model-call admission, transport, accounting, and projection."""

from __future__ import annotations

from dataclasses import replace
from time import monotonic
from typing import Any, Dict, cast

from agent.llm.contracts import ModelRequest, ModelResponse, TokenUsage, response_text, response_usage
from agent.llm.legacy_payload import legacy_payload
from agent.runtime.budget import estimate_model_request_tokens
from agent.runtime.budget_estimation import RequestInputMeasurement
from agent.runtime.context import TaskExecutionContext
from agent.runtime.logging import logger
from agent.runtime.model_call_record import (
    ModelCallOutcome,
    ModelCallRecord,
    finalize_record,
)
from agent.runtime.model_call_stream import (
    consume_events,
    consume_legacy_request,
    observed_stream_response,
)
from agent.runtime.model_call_support import context_for_session
from agent.runtime.task_policy import TaskPolicyError


class ModelCallService:
    """Own the common model-call lifecycle while delegating transport details."""

    def __init__(self, context: TaskExecutionContext, *, session: Any = None) -> None:
        self.context = context
        self.session = session

    @classmethod
    def for_context(cls, context: TaskExecutionContext) -> "ModelCallService":
        return cls(context)

    @classmethod
    def for_session(cls, session: Any) -> "ModelCallService":
        return cls(context_for_session(session), session=session)

    @property
    def gateway(self) -> Any:
        return self.context.model_gateway

    def _admit(self, request: Any) -> tuple[RequestInputMeasurement, int, int, float]:
        policy = getattr(self.context, "task_policy", None)
        if policy is not None:
            preliminary = policy.check_current(resource="model")
            if preliminary.denied:
                raise TaskPolicyError(preliminary)
        measurement = self.context.measure_request_input_tokens(request)
        output_limit = getattr(request, "max_output_tokens", 0)
        if isinstance(output_limit, bool) or not isinstance(output_limit, int):
            output_limit = 0
        allowance = max(1, (measurement.token_count or 0) + max(0, output_limit))
        if policy is not None:
            decision = policy.check_current(
                resource="model",
                token_allowance=allowance,
            )
            if decision.denied:
                raise TaskPolicyError(decision)
        call_number = self.context.consume_model_call(
            request,
            token_allowance=allowance,
            request_input_measurement=measurement,
        )
        reserved = self.context.reservation_for_model_call(call_number)
        return measurement, call_number, reserved, monotonic()

    def _estimate(
        self,
        request: Any,
        response: Any,
        measurement: RequestInputMeasurement,
        usage: Any,
    ) -> int:
        return estimate_model_request_tokens(
            request,
            response,
            request_input_measurement=measurement,
            gateway=self.gateway,
            usage=usage,
        )

    def _record(self, **kwargs: Any) -> ModelCallRecord:
        return finalize_record(self, **kwargs)

    def _start_event(self, operation: str, call_number: int) -> None:
        try:
            self.context.emit(
                "model_call_started",
                {
                    "operation": operation,
                    "provider": getattr(self.gateway, "provider_name", None),
                    "call_number": call_number,
                },
            )
        except Exception as exc:
            logger.warning(
                "Falha ao emitir início de chamada do modelo: %s", type(exc).__name__
            )

    @staticmethod
    def _coerce_response(response: Any) -> ModelResponse:
        if isinstance(response, ModelResponse):
            return response
        usage = response_usage(response)
        return ModelResponse(
            content=response_text(response),
            usage=usage if usage is not None else TokenUsage(available=False),
        )

    def _complete_provider(self, request: Any) -> Any:
        complete = getattr(self.gateway, "complete", None)
        if callable(complete):
            return complete(request)
        payload = legacy_payload(self.gateway, request)
        complete_payload = getattr(self.gateway, "complete_payload", None)
        if callable(complete_payload):
            return complete_payload(payload)
        send_payload = getattr(self.gateway, "send_payload", None)
        if callable(send_payload):
            return send_payload(payload, stream=False)
        raise AttributeError("gateway has no supported completion transport")

    def _outcome(
        self,
        response: Any,
        record: ModelCallRecord,
        call_number: int,
        usage: Any,
        text: str,
    ) -> ModelCallOutcome:
        return ModelCallOutcome(
            response=response,
            record=record,
            call_number=call_number,
            usage=usage,
            text=text,
        )

    def complete(
        self,
        request: ModelRequest,
        *,
        operation: str = "model_call",
    ) -> ModelCallOutcome:
        measurement, call_number, reserved, started = self._admit(request)
        self._start_event(operation, call_number)
        try:
            with self.context.model_slot():
                response = self._coerce_response(self._complete_provider(request))
            usage = response_usage(response)
            estimate = self._estimate(request, response, measurement, usage)
        except BaseException:
            estimate = self._estimate(request, None, measurement, None)
            self._record(
                request=request,
                started_at=started,
                call_number=call_number,
                reserved_tokens=reserved,
                measurement=measurement,
                estimated_tokens=estimate,
                success=False,
                streaming=False,
                response=None,
                usage=None,
                operation=operation,
            )
            raise
        record = self._record(
            request=request,
            started_at=started,
            call_number=call_number,
            reserved_tokens=reserved,
            measurement=measurement,
            estimated_tokens=estimate,
            success=True,
            streaming=False,
            response=response,
            usage=usage,
            operation=operation,
        )
        return self._outcome(response, record, call_number, usage, response.content)

    def stream(
        self,
        request: ModelRequest,
        callbacks: Dict[str, Any],
        *,
        operation: str = "model_stream",
    ) -> ModelCallOutcome:
        request = replace(request, stream=True)
        measurement, call_number, reserved, started = self._admit(request)
        self._start_event(operation, call_number)
        visible = ""
        usage: Any = None
        try:
            with self.context.model_slot():
                if callable(getattr(self.gateway, "stream", None)):
                    visible, usage = consume_events(
                        self.gateway.stream(request),
                        callbacks,
                    )
                else:
                    visible, usage = consume_legacy_request(
                        self.gateway,
                        request,
                        callbacks,
                    )
        except BaseException as exc:
            captured_usage = getattr(exc, "stream_usage", None)
            if captured_usage is not None:
                usage = captured_usage
            partial = getattr(exc, "partial_content", None)
            if isinstance(partial, str) and partial:
                visible = partial
            observed = observed_stream_response(visible, usage)
            estimate = self._estimate(request, observed, measurement, usage)
            self._record(
                request=request,
                started_at=started,
                call_number=call_number,
                reserved_tokens=reserved,
                measurement=measurement,
                estimated_tokens=estimate,
                success=False,
                streaming=True,
                response=observed,
                usage=usage,
                operation=operation,
            )
            raise
        observed = observed_stream_response(visible, usage)
        estimate = self._estimate(request, observed, measurement, usage)
        record = self._record(
            request=request,
            started_at=started,
            call_number=call_number,
            reserved_tokens=reserved,
            measurement=measurement,
            estimated_tokens=estimate,
            success=True,
            streaming=True,
            response=observed,
            usage=usage,
            operation=operation,
        )
        response = ModelResponse(
            content=visible,
            usage=usage if usage is not None else TokenUsage(available=False),
        )
        return self._outcome(response, record, call_number, usage, visible.strip())

    def start_legacy_request(
        self,
        payload: Dict[str, Any],
        *,
        stream: bool,
    ) -> Any:
        from agent.runtime.model_call_legacy import start_legacy_request

        return start_legacy_request(
            self,
            payload,
            stream=stream,
            operation="legacy_request",
        )

    def consume_pending(
        self,
        pending: Any,
        callbacks: Dict[str, Any],
    ) -> ModelCallOutcome:
        from agent.runtime.model_call_legacy import consume_pending_stream

        return cast(ModelCallOutcome, consume_pending_stream(self, pending, callbacks))

    def consume_external_stream(
        self,
        response: Any,
        callbacks: Dict[str, Any],
    ) -> str:
        from agent.runtime.model_call_legacy import consume_external_stream

        return consume_external_stream(self, response, callbacks)


__all__ = ["ModelCallOutcome", "ModelCallRecord", "ModelCallService"]

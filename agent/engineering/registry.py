"""Deterministic immutable Engineering operation registry."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Iterable, Mapping

from agent.engineering.contracts import (
    EngineeringBackendOutcome,
    EngineeringBackendStatus,
    EngineeringEffects,
    EngineeringExecutionContext,
    EngineeringOperationDescriptor,
    EngineeringOperationViewV1,
    EngineeringRunPhase,
    EngineeringRunResultV1,
    EngineeringScope,
    EngineeringTerminalStatus,
    order_reason_codes,
)
from agent.engineering.summary import normalize_summary, validate_references


@dataclass(frozen=True)
class EngineeringTerminalIntent:
    status: EngineeringTerminalStatus
    reason_codes: tuple[str, ...]
    summary: Mapping[str, object]
    references: tuple


def terminal_intent(admission: Any, outcome: EngineeringBackendOutcome | None, indeterminate: bool, internal_indeterminate: bool, protocol_error: bool, cancelled: bool, deadline: bool) -> EngineeringTerminalIntent:
    if indeterminate:
        reasons = ['ENGINEERING_BACKEND_STATE_INDETERMINATE']
        reasons.extend((code for condition, code in ((internal_indeterminate, 'ENGINEERING_INTERNAL_ERROR'), (cancelled, 'ENGINEERING_CANCELLED'), (deadline, 'ENGINEERING_DEADLINE_EXCEEDED')) if condition))
        return EngineeringTerminalIntent(EngineeringTerminalStatus.UNVERIFIED, order_reason_codes(reasons), {}, ())
    if cancelled:
        reasons = ['ENGINEERING_CANCELLED'] + (['ENGINEERING_DEADLINE_EXCEEDED'] if deadline else [])
        return EngineeringTerminalIntent(EngineeringTerminalStatus.FAILED, order_reason_codes(reasons), {}, ())
    if deadline:
        return EngineeringTerminalIntent(EngineeringTerminalStatus.FAILED, ('ENGINEERING_DEADLINE_EXCEEDED',), {}, ())
    if protocol_error or outcome is None:
        code = 'ENGINEERING_BACKEND_RESULT_INVALID' if protocol_error else 'ENGINEERING_INTERNAL_ERROR'
        return EngineeringTerminalIntent(EngineeringTerminalStatus.PROTOCOL_ERROR if protocol_error else EngineeringTerminalStatus.FAILED, (code,), {}, ())
    validated = validated_outcome(admission, outcome)
    if validated is None:
        return EngineeringTerminalIntent(EngineeringTerminalStatus.PROTOCOL_ERROR, ('ENGINEERING_BACKEND_RESULT_INVALID',), {}, ())
    summary, references = validated
    status = EngineeringTerminalStatus.FAILED if outcome.status is EngineeringBackendStatus.FAILED else EngineeringTerminalStatus.SUCCEEDED
    return EngineeringTerminalIntent(status, ('ENGINEERING_BACKEND_FAILED',) if status is EngineeringTerminalStatus.FAILED else (), summary, references)


def validated_outcome(admission: Any, outcome: EngineeringBackendOutcome) -> tuple[Mapping[str, object], tuple] | None:
    if not isinstance(outcome.status, EngineeringBackendStatus) or not isinstance(outcome.live_model_used, bool) or not isinstance(outcome.network_used, bool):
        return None
    effects = admission.descriptor.effects
    if (outcome.live_model_used and not effects.real_model) or (outcome.network_used and not effects.network) or (effects.hermetic and (outcome.live_model_used or outcome.network_used)):
        return None
    try:
        return normalize_summary(outcome.summary), validate_references(outcome.references)
    except (TypeError, ValueError):
        return None


def non_durable(active: Any, admission: Any, reasons: list[str]) -> EngineeringRunResultV1:
    return EngineeringRunResultV1(run_id=active.run_id, operation_id=active.operation_id, phase=EngineeringRunPhase.TERMINAL, status=EngineeringTerminalStatus.UNVERIFIED, reason_codes=order_reason_codes(reasons), started_at_utc=active.started_at_utc, finished_at_utc=active.started_at_utc, duration_ms=None, workspace_id=active.workspace_id, persisted=False, summary={}, references=(), environment=admission.environment)

ACCEPTANCE_INSTALLED_PACKAGE = EngineeringOperationDescriptor(
    operation_id="acceptance.installed-package",
    scope=EngineeringScope.SOURCE_REPOSITORY,
    effects=EngineeringEffects(
        network=True, real_model=False, managed_external_process=True, mutating_or_executing=True, hermetic=False
    ),
    parameter_schema=MappingProxyType(
        {"type": "object", "properties": MappingProxyType({}), "required": (), "additionalProperties": False}
    ),
)

EVALUATION_PRACTICAL = EngineeringOperationDescriptor(
    "evaluation.practical-v1",
    EngineeringScope.SOURCE_REPOSITORY,
    EngineeringEffects(False, False, False, False, True),
    MappingProxyType({
        "type": "object",
        "properties": MappingProxyType({
            "profile": {"type": "string", "enum": ("current", "persona-reference-w18")},
            "fault": {
                "type": "object",
                "properties": MappingProxyType({
                    "schema_version": {"type": "integer", "minimum": 1, "maximum": 1},
                    "steps": {
                        "type": "array",
                        "maxItems": 16,
                        "items": {
                            "type": "object",
                            "properties": MappingProxyType({
                                "call_index": {"type": "integer", "minimum": 1, "maximum": 16},
                                "effect": {"type": "string", "enum": ("timeout", "provider_error", "invalid_structured_response")},
                            }),
                            "required": ("call_index", "effect"),
                            "additionalProperties": False,
                        },
                    },
                }),
                "required": ("schema_version", "steps"),
                "additionalProperties": False,
            },
        }),
        "required": (),
        "additionalProperties": False,
    }),
    model_safe=False,
)
HEALTH_OFFLINE = EngineeringOperationDescriptor(
    "health.offline",
    EngineeringScope.WORKSPACE,
    EngineeringEffects(False, False, False, False, True),
    MappingProxyType({"type": "object", "properties": MappingProxyType({}), "required": (), "additionalProperties": False}),
    model_safe=True,
)
INSPECTION_COMPLETED = EngineeringOperationDescriptor(
    "inspection.completed-run",
    EngineeringScope.WORKSPACE,
    EngineeringEffects(False, False, False, False, True),
        MappingProxyType({
            "type": "object",
            "properties": MappingProxyType({
                "trace_run_id": {"type": "string", "minLength": 1, "maxLength": 192},
                "limit": {"type": "integer", "minimum": 1, "maximum": 64},
            }),
            "required": ("trace_run_id", "limit"),
            "additionalProperties": False,
        }),
    model_safe=True,
)


class EngineeringRegistry:
    def __init__(self, descriptors: Iterable[EngineeringOperationDescriptor]) -> None:
        values: dict[str, EngineeringOperationDescriptor] = {}
        for descriptor in descriptors:
            if descriptor.operation_id in values:
                raise ValueError(f"duplicate Engineering operation: {descriptor.operation_id}")
            values[descriptor.operation_id] = descriptor
        self._descriptors: Mapping[str, EngineeringOperationDescriptor] = MappingProxyType(values)

    def get(self, operation_id: str) -> EngineeringOperationDescriptor | None:
        return self._descriptors.get(operation_id)

    def descriptors(self) -> tuple[EngineeringOperationDescriptor, ...]:
        return tuple(self._descriptors[key] for key in sorted(self._descriptors))

    def view(
        self,
        descriptor: EngineeringOperationDescriptor,
        context: EngineeringExecutionContext,
        *,
        backend_available: bool = True,
    ) -> EngineeringOperationViewV1:
        available, reason = True, None
        if descriptor.scope is EngineeringScope.WORKSPACE and context.workspace is None:
            available, reason = False, "ENGINEERING_WORKSPACE_REQUIRED"
        elif descriptor.scope is EngineeringScope.SOURCE_REPOSITORY and context.source_repository is None:
            available, reason = False, "ENGINEERING_SOURCE_REPOSITORY_REQUIRED"
        elif not backend_available:
            available, reason = False, "ENGINEERING_OPERATION_UNAVAILABLE"
        return EngineeringOperationViewV1(
            descriptor.operation_id,
            descriptor.scope,
            descriptor.effects,
            available,
            reason,
            descriptor.parameter_schema,
            model_safe=descriptor.model_safe,
        )


def production_registry() -> EngineeringRegistry:
    return EngineeringRegistry((ACCEPTANCE_INSTALLED_PACKAGE, EVALUATION_PRACTICAL, HEALTH_OFFLINE, INSPECTION_COMPLETED))


__all__ = ["ACCEPTANCE_INSTALLED_PACKAGE", "EVALUATION_PRACTICAL", "HEALTH_OFFLINE", "INSPECTION_COMPLETED", "EngineeringRegistry", "production_registry"]

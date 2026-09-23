"""Backend-injected Engineering orchestration."""

from __future__ import annotations

from threading import BoundedSemaphore
from types import MappingProxyType
from typing import Any, Mapping, Protocol

from agent.engineering.contracts import (
    EngineeringBackend,
    EngineeringBackendOutcome,
    EngineeringBackendProtocolError,
    EngineeringBackendStateIndeterminateError,
    EngineeringErrorV1,
    EngineeringExecutionContext,
    EngineeringExecutionResponseV1,
    EngineeringOperationViewV1,
    EngineeringQueryStatus,
    EngineeringRequest,
    EngineeringRunResultV1,
    EngineeringRunSummaryV1,
    EngineeringStoreError,
)
from agent.engineering.policy import (
    EngineeringPreflight,
    error,
    preflight,
    safe_point_error,
)
from agent.engineering.registry import (
    EngineeringRegistry,
    EngineeringTerminalIntent,
    non_durable,
    terminal_intent,
)
from agent.tools.contracts import ToolAdapter as ToolAdapter
from agent.tools.contracts import ToolDescriptor, ToolError, ToolInvocation, ToolResult, ToolStatus

MAX_CONCURRENT_HERMETIC_RUNS = 2
ENGINEERING_HERMETIC_ADMISSION_POLL_SECONDS = 0.05
ENGINEERING_HERMETIC_ADMISSION_MAX_WAIT_SECONDS = 5.0

class EngineeringStore(Protocol):
    def begin(self, admission: EngineeringPreflight, context: EngineeringExecutionContext) -> EngineeringRunSummaryV1:
        ...
    def publish_managed_guard(self, active: EngineeringRunSummaryV1, admission: EngineeringPreflight, context: EngineeringExecutionContext) -> None:
        ...
    def finish(self, active: EngineeringRunSummaryV1, admission: EngineeringPreflight, intent: EngineeringTerminalIntent, context: EngineeringExecutionContext) -> EngineeringRunResultV1:
        ...
    def release_local_ownership(self, active: EngineeringRunSummaryV1) -> None:
        ...


class EngineeringLifecycleCoordinator:
    """Coordinate lifecycle calls while the store owns durable mechanics."""

    def __init__(self, store: EngineeringStore) -> None:
        self._store = store

    def publish_managed_guard(self, active: EngineeringRunSummaryV1, admission: EngineeringPreflight, context: EngineeringExecutionContext) -> None:
        self._store.publish_managed_guard(active, admission, context)

    def finish(self, active: EngineeringRunSummaryV1, admission: EngineeringPreflight, intent: EngineeringTerminalIntent, context: EngineeringExecutionContext) -> EngineeringRunResultV1:
        return self._store.finish(active, admission, intent, context)


class EngineeringService:
    def __init__(self, registry: EngineeringRegistry, backends: Mapping[str, EngineeringBackend], store: EngineeringStore) -> None:
        copied = dict(backends)
        known = {item.operation_id for item in registry.descriptors()}
        if set(copied) != known:
            raise ValueError('backend keys must exactly match registry descriptors')
        self._registry = registry
        self._backends = MappingProxyType(copied)
        self._store = store
        self._lifecycle = EngineeringLifecycleCoordinator(store)
        self._hermetic_semaphore = BoundedSemaphore(MAX_CONCURRENT_HERMETIC_RUNS)
    def list_operations(self, context: EngineeringExecutionContext) -> tuple[EngineeringOperationViewV1, ...]:
        return tuple((self._registry.view(item, context, backend_available=item.operation_id in self._backends) for item in self._registry.descriptors()))
    def describe(self, operation_id: str, context: EngineeringExecutionContext) -> EngineeringOperationViewV1 | EngineeringErrorV1:
        descriptor = self._registry.get(operation_id)
        if descriptor is None:
            return error('ENGINEERING_OPERATION_NOT_FOUND')
        return self._registry.view(descriptor, context, backend_available=operation_id in self._backends)
    def preflight(self, request: EngineeringRequest, context: EngineeringExecutionContext) -> EngineeringPreflight | EngineeringErrorV1:
        return preflight(request, context, self._registry, backend_available=request.operation_id in self._backends)
    def run(self, request: EngineeringRequest, context: EngineeringExecutionContext) -> EngineeringExecutionResponseV1:
        admission = self.preflight(request, context)
        if isinstance(admission, EngineeringErrorV1):
            return admission
        acquired = False
        if admission.descriptor.effects.hermetic:
            acquired = self._acquire_hermetic(context)
            if not acquired:
                return safe_point_error(context) or error('ENGINEERING_LOCK_BUSY')
        try:
            return self._run_admitted(admission, context)
        finally:
            if acquired:
                self._hermetic_semaphore.release()
    def _acquire_hermetic(self, context: EngineeringExecutionContext) -> bool:
        started = context.monotonic_now()
        while context.monotonic_now() - started < ENGINEERING_HERMETIC_ADMISSION_MAX_WAIT_SECONDS:
            if safe_point_error(context) is not None:
                return False
            if self._hermetic_semaphore.acquire(timeout=ENGINEERING_HERMETIC_ADMISSION_POLL_SECONDS):
                if safe_point_error(context) is None:
                    return True
                self._hermetic_semaphore.release()
                return False
        return False
    def _run_admitted(self, admission: EngineeringPreflight, context: EngineeringExecutionContext) -> EngineeringExecutionResponseV1:
        safe = safe_point_error(context)
        if safe is not None:
            return safe
        started = self._begin(admission, context)
        if isinstance(started, EngineeringErrorV1):
            return started
        active = started
        if admission.descriptor.effects.managed_external_process:
            try:
                self._lifecycle.publish_managed_guard(active, admission, context)
            except EngineeringStoreError as exc:
                reasons = ['ENGINEERING_RESULT_PERSIST_FAILED']
                if exc.code == 'ENGINEERING_DURABLE_STATE_INDETERMINATE':
                    reasons.append(exc.code)
                self._store.release_local_ownership(active)
                return non_durable(active, admission, reasons)
        safe_after_active = safe_point_error(context)
        outcome, indeterminate, internal_indeterminate, protocol_error = self._execute_backend(admission, context, safe_after_active)
        cancelled = bool(context.cancellation_token and context.cancellation_token.is_set())
        deadline = context.deadline_monotonic is not None and context.monotonic_now() >= context.deadline_monotonic
        if safe_after_active is not None:
            cancelled = bool(context.cancellation_token and context.cancellation_token.is_set())
            deadline = context.deadline_monotonic is not None and context.monotonic_now() >= context.deadline_monotonic
        intent = terminal_intent(admission, outcome, indeterminate, internal_indeterminate, protocol_error, cancelled, deadline)
        try:
            return self._lifecycle.finish(active, admission, intent, context)
        except EngineeringStoreError as exc:
            reasons = list(intent.reason_codes) + ['ENGINEERING_RESULT_PERSIST_FAILED']
            if exc.code == 'ENGINEERING_DURABLE_STATE_INDETERMINATE':
                reasons.append(exc.code)
            return non_durable(active, admission, reasons)
    def _begin(self, admission: EngineeringPreflight, context: EngineeringExecutionContext) -> EngineeringRunSummaryV1 | EngineeringErrorV1:
        try:
            return self._store.begin(admission, context)
        except EngineeringStoreError as exc:
            allowed = {'ENGINEERING_RESULT_PERSIST_FAILED', 'ENGINEERING_DURABLE_STATE_INDETERMINATE', 'ENGINEERING_STORE_STATE_UNSAFE', 'ENGINEERING_STORE_CAPACITY_EXCEEDED', 'ENGINEERING_LOCK_BUSY', 'ENGINEERING_BACKEND_STATE_INDETERMINATE', 'ENGINEERING_CANCELLED', 'ENGINEERING_DEADLINE_EXCEEDED', 'ENGINEERING_RUN_RECORD_CORRUPT'}
            code = exc.code if exc.code in allowed else 'ENGINEERING_INTERNAL_ERROR'
            unverified = {'ENGINEERING_RESULT_PERSIST_FAILED', 'ENGINEERING_DURABLE_STATE_INDETERMINATE', 'ENGINEERING_BACKEND_STATE_INDETERMINATE'}
            return error(code, query_status=EngineeringQueryStatus.UNVERIFIED if code in unverified else EngineeringQueryStatus.BLOCKED, run_id=exc.candidate_run_id if code == 'ENGINEERING_DURABLE_STATE_INDETERMINATE' else None)
    def _execute_backend(self, admission: EngineeringPreflight, context: EngineeringExecutionContext, safe: EngineeringErrorV1 | None) -> tuple[EngineeringBackendOutcome | None, bool, bool, bool]:
        if safe is not None:
            return (None, False, False, False)
        try:
            return (self._backends[admission.descriptor.operation_id].execute(admission.request, context), False, False, False)
        except EngineeringBackendStateIndeterminateError:
            return (None, True, False, False)
        except EngineeringBackendProtocolError:
            return (None, False, False, True)
        except Exception:
            managed = admission.descriptor.effects.managed_external_process
            return (None, managed, managed, False)


class ModelSafeToolAdapter:
    """Adapt one public model-safe owner to the canonical ToolAdapter seam."""

    def __init__(self, owner: Any, descriptor: ToolDescriptor, context_factory: Any) -> None:
        self.owner = owner
        self._descriptor = descriptor
        self._context_factory = context_factory

    def descriptors(self) -> tuple[ToolDescriptor, ...]:
        return (self._descriptor,)

    def invoke(self, invocation: ToolInvocation) -> ToolResult:
        response = self.owner.invoke(invocation.tool_name, invocation.args, self._context_factory())
        status_value = getattr(response.status, "value", response.status)
        if status_value == "available":
            return ToolResult(invocation_id=invocation.invocation_id, status=ToolStatus.SUCCEEDED, data=response.data, executed=False)
        status = ToolStatus.UNAVAILABLE if status_value in {"unavailable", "degraded"} else ToolStatus.FAILED
        reason = response.reason_code or "ENGINEERING_OPERATION_UNAVAILABLE"
        return ToolResult(invocation_id=invocation.invocation_id, status=status, error=ToolError(reason, response.reason_code or "Engineering operation unavailable."), executed=False)

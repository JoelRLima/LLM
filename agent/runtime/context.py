"""Contexto e resultado tipados para casos de uso e subtarefas."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from threading import BoundedSemaphore
from typing import Any, Dict, Mapping, Optional, Protocol, TypeAlias, cast

from agent.cancellation import CancellationToken
from agent.llm.contracts import ModelGateway
from agent.llm.model_profile import ResolvedModelProfile
from agent.llm.model_profile_binding import cached_gateway_model_profile
from agent.runtime.budget import (
    BudgetSnapshot,
    TaskBudgetLedger,
    estimate_model_request_allowance,
)
from agent.runtime.budget_estimation import (
    RequestInputMeasurement,
    measure_model_request_input_tokens,
)
from agent.runtime.context_results import Artifact, TaskResult, TaskStatus
from agent.runtime.context_tools import CorrelatedToolRequestMixin
from agent.runtime.correlation import RunCorrelation
from agent.runtime.event_dispatch import dispatch_runtime_event
from agent.runtime.events import RuntimeEvent
from agent.runtime.limits import default_runtime_limit, runtime_limit_values
from agent.runtime.recovery import RecoveryBudgetState
from agent.runtime.task_policy import TaskPolicyState, TaskRuntimePolicy, bind_task_execution_context

__all__ = ["Artifact", "RuntimeLimits", "TaskExecutionContext", "TaskResult", "TaskStatus"]


class EventSink(Protocol):
    def emit(self, event: RuntimeEvent) -> None:
        ...


class MetricsSink(Protocol):
    def record(self, metric: Dict[str, Any]) -> None:
        ...


class NullEventSink:
    def emit(self, event: RuntimeEvent) -> None:
        del event


class NullMetricsSink:
    def record(self, metric: Dict[str, Any]) -> None:
        del metric


class ModelConcurrencyGate:
    """Semáforo compartilhado entre contextos pai/filho."""

    def __init__(self, limit: int = 1) -> None:
        self._semaphore = BoundedSemaphore(max(1, limit))

    def __enter__(self) -> "ModelConcurrencyGate":
        self._semaphore.acquire()
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        del exc_type, exc, traceback
        self._semaphore.release()


class ProcessConcurrencyGate(ModelConcurrencyGate):
    """Semáforo compartilhado para processos de validação."""


ModelCallBudget: TypeAlias = TaskBudgetLedger


@dataclass(frozen=True)
class RuntimeLimits:
    max_model_concurrency: int = field(default_factory=lambda: default_runtime_limit("max_model_concurrency"))
    max_io_concurrency: int = field(default_factory=lambda: default_runtime_limit("max_io_concurrency"))
    max_process_concurrency: int = field(default_factory=lambda: default_runtime_limit("max_process_concurrency"))
    max_steps: int = field(default_factory=lambda: default_runtime_limit("max_steps"))
    max_model_calls: int = field(default_factory=lambda: default_runtime_limit("max_model_calls"))
    max_task_tool_calls: int = field(default_factory=lambda: default_runtime_limit("max_task_tool_calls"))
    max_task_tokens: int = field(default_factory=lambda: default_runtime_limit("max_task_tokens"))
    max_task_wall_seconds: int = field(default_factory=lambda: default_runtime_limit("max_task_wall_seconds"))
    max_repeated_no_progress: int = field(default_factory=lambda: default_runtime_limit("max_repeated_no_progress"))
    max_consecutive_same_error: int = field(default_factory=lambda: default_runtime_limit("max_consecutive_same_error"))
    max_reasoning_turns: int = field(default_factory=lambda: default_runtime_limit("max_reasoning_turns"))
    max_output_tokens: int = field(default_factory=lambda: default_runtime_limit("max_output_tokens"))
    max_repair_attempts: int = field(default_factory=lambda: default_runtime_limit("max_repair_attempts"))

    @classmethod
    def from_config(cls, config: Mapping[str, Any] | None = None) -> "RuntimeLimits":
        """Materialize limits through one typed config boundary."""

        return cls(**runtime_limit_values(config))


@dataclass(frozen=True)
class TaskExecutionContext(CorrelatedToolRequestMixin):
    model_gateway: ModelGateway
    cancellation: CancellationToken
    model_profile: ResolvedModelProfile | None = None
    limits: RuntimeLimits = field(default_factory=RuntimeLimits)
    event_sink: EventSink | None = field(default_factory=NullEventSink)
    metrics_sink: MetricsSink = field(default_factory=NullMetricsSink)
    model_gate: Optional[ModelConcurrencyGate] = None
    process_gate: Optional[ProcessConcurrencyGate] = None
    model_call_budget: Optional[ModelCallBudget] = None
    budget_ledger: Optional[TaskBudgetLedger] = None
    correlation: RunCorrelation | None = None
    task_id: str | None = None
    parent_task_id: Optional[str] = None
    node_id: Optional[str] = None
    permissions: frozenset[str] = frozenset()
    metadata: Dict[str, Any] = field(default_factory=dict)
    policy_state: TaskPolicyState | None = None
    recovery_budget: RecoveryBudgetState | None = None
    task_policy: TaskRuntimePolicy | None = None

    def __post_init__(self) -> None:
        correlation = self.correlation
        if correlation is None:
            correlation = RunCorrelation.fresh(root_task_id=self.task_id)
        object.__setattr__(self, "correlation", correlation)
        object.__setattr__(self, "task_id", correlation.task_id)
        object.__setattr__(self, "parent_task_id", correlation.parent_task_id)
        object.__setattr__(self, "node_id", correlation.node_id)
        if self.model_profile is None:
            resolved_profile = getattr(self.model_gateway, "resolved_profile", None)
            if not isinstance(resolved_profile, ResolvedModelProfile):
                resolved_profile = cached_gateway_model_profile(self.model_gateway)
            if isinstance(resolved_profile, ResolvedModelProfile):
                object.__setattr__(self, "model_profile", resolved_profile)
        if self.model_gate is None:
            object.__setattr__(
                self,
                "model_gate",
                ModelConcurrencyGate(self.limits.max_model_concurrency),
            )
        if self.process_gate is None:
            object.__setattr__(
                self,
                "process_gate",
                ProcessConcurrencyGate(self.limits.max_process_concurrency),
            )
        if self.model_call_budget is not None and self.budget_ledger is not None:
            if self.model_call_budget is not self.budget_ledger:
                raise ValueError("model_call_budget and budget_ledger must be the same object")
        ledger = self.budget_ledger or self.model_call_budget
        if ledger is None:
            ledger = TaskBudgetLedger(
                max_model_calls=self.limits.max_model_calls,
                max_task_tool_calls=self.limits.max_task_tool_calls,
                max_task_tokens=self.limits.max_task_tokens,
            )
        object.__setattr__(self, "budget_ledger", ledger)
        object.__setattr__(self, "model_call_budget", ledger)
        bind_task_execution_context(self, ledger=ledger, correlation=correlation)
    @property
    def run_id(self) -> str:
        correlation = self.correlation
        if correlation is None:
            raise RuntimeError("task execution context has no runtime correlation")
        return str(correlation.run_id)

    @property
    def root_task_id(self) -> str:
        correlation = self.correlation
        if correlation is None:
            raise RuntimeError("task execution context has no runtime correlation")
        return str(correlation.root_task_id)

    def _runtime_correlation(self) -> RunCorrelation:
        correlation = self.correlation
        if correlation is None:
            raise RuntimeError("task execution context has no runtime correlation")
        return correlation

    def child(self, node_id: str, permissions: Optional[frozenset[str]] = None) -> "TaskExecutionContext":
        requested = self.permissions if permissions is None else frozenset(permissions)
        if not requested.issubset(self.permissions):
            missing = ", ".join(sorted(requested - self.permissions))
            raise PermissionError(
                f"Child context requests capabilities outside its parent authority: {missing}"
            )
        child_correlation = self._runtime_correlation().child(node_id)
        return replace(
            self,
            correlation=child_correlation,
            task_id=child_correlation.task_id,
            parent_task_id=child_correlation.parent_task_id,
            node_id=child_correlation.node_id,
            permissions=requested,
            metadata=dict(self.metadata),
        )

    def new_task(self) -> "TaskExecutionContext":
        """Start a new task identity while preserving runtime ownership."""

        task_correlation = self._runtime_correlation().unrelated_task()
        return replace(
            self,
            correlation=task_correlation,
            task_id=task_correlation.task_id,
            parent_task_id=task_correlation.parent_task_id,
            node_id=task_correlation.node_id,
            metadata=dict(self.metadata),
        )

    def emit(self, event_type: str, data: Optional[Dict[str, Any]] = None) -> None:
        correlation = self.correlation
        if correlation is None:
            raise RuntimeError("task execution context has no runtime correlation")
        dispatch_runtime_event(
            self.event_sink,
            RuntimeEvent.from_fields(event_type, correlation, data),
        )

    def record_metric(self, metric_type: str, data: Optional[Dict[str, Any]] = None) -> None:
        correlation = self._runtime_correlation()
        self.metrics_sink.record(
            {
                "metric_type": metric_type,
                "run_id": correlation.run_id,
                "root_task_id": correlation.root_task_id,
                "task_id": self.task_id,
                "parent_task_id": self.parent_task_id,
                "node_id": self.node_id,
                **(data or {}),
            }
        )

    def measure_request_input_tokens(self, request: Any) -> RequestInputMeasurement:
        """Use the one canonical request-input measurement primitive."""

        return measure_model_request_input_tokens(request, self.model_gateway)

    def consume_model_call(
        self,
        request: Any = None,
        *,
        token_allowance: int | None = None,
        request_input_measurement: RequestInputMeasurement | None = None,
    ) -> int:
        ledger = self.budget_ledger
        if ledger is None:  # Apenas para estreitar o tipo após __post_init__.
            raise RuntimeError("Orçamento de modelo não inicializado.")
        if token_allowance is None and request is not None:
            measurement = request_input_measurement or self.measure_request_input_tokens(
                request
            )
            token_allowance = estimate_model_request_allowance(
                request,
                request_input_measurement=measurement,
            )
        return cast(int, ledger.reserve_model_call(token_allowance or 0))

    def reservation_for_model_call(self, call_number: int) -> int:
        ledger = self.budget_ledger
        if ledger is None:
            raise RuntimeError("Ledger de tarefa nao inicializado.")
        return cast(int, ledger.reservation_for(call_number))

    def finalize_model_call(
        self, call_number: int, *, usage: Any = None, estimated_tokens: int = 0
    ) -> None:
        ledger = self.budget_ledger
        if ledger is None:
            raise RuntimeError("Ledger de tarefa não inicializado.")
        ledger.finalize_model_call(
            call_number,
            usage=usage,
            estimated_tokens=estimated_tokens,
        )

    def reserve_tool_call(self) -> int:
        ledger = self.budget_ledger
        if ledger is None:
            raise RuntimeError("Ledger de tarefa não inicializado.")
        return cast(int, ledger.reserve_tool_call())

    def budget_snapshot(self) -> BudgetSnapshot:
        ledger = self.budget_ledger
        if ledger is None:
            raise RuntimeError("Ledger de tarefa não inicializado.")
        return ledger.snapshot()

    def model_slot(self) -> ModelConcurrencyGate:
        gate = self.model_gate
        if gate is None:
            raise RuntimeError("Gate de modelo não inicializado.")
        return gate


    def process_slot(self) -> ProcessConcurrencyGate:
        gate = self.process_gate
        if gate is None:
            raise RuntimeError("Gate de processos não inicializado.")
        return gate

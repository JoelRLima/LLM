"""Contexto e resultado tipados para casos de uso e subtarefas."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from threading import BoundedSemaphore
from typing import Any, Dict, Mapping, Optional, Protocol
from uuid import uuid4

from agent.cancellation import CancellationToken
from agent.llm.contracts import ModelGateway
from agent.runtime.budget import (
    BudgetSnapshot,
    TaskBudgetLedger,
    estimate_model_request_allowance,
)
from agent.runtime.budget_estimation import (
    RequestInputMeasurement,
    measure_model_request_input_tokens,
)
from agent.runtime.limits import default_runtime_limit, runtime_limit_values
from agent.runtime.outcome_taxonomy import OperationalStatus


class EventSink(Protocol):
    def emit(self, event_type: str, data: Dict[str, Any]) -> None:
        ...


class MetricsSink(Protocol):
    def record(self, metric: Dict[str, Any]) -> None:
        ...


class NullEventSink:
    def emit(self, event_type: str, data: Dict[str, Any]) -> None:
        del event_type, data


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


ModelCallBudget = TaskBudgetLedger


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
class TaskExecutionContext:
    model_gateway: ModelGateway
    cancellation: CancellationToken
    limits: RuntimeLimits = field(default_factory=RuntimeLimits)
    event_sink: EventSink = field(default_factory=NullEventSink)
    metrics_sink: MetricsSink = field(default_factory=NullMetricsSink)
    model_gate: Optional[ModelConcurrencyGate] = None
    process_gate: Optional[ProcessConcurrencyGate] = None
    model_call_budget: Optional[ModelCallBudget] = None
    budget_ledger: Optional[TaskBudgetLedger] = None
    task_id: str = field(default_factory=lambda: uuid4().hex)
    parent_task_id: Optional[str] = None
    node_id: Optional[str] = None
    permissions: frozenset[str] = frozenset()
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
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

    def child(self, node_id: str, permissions: Optional[frozenset[str]] = None) -> "TaskExecutionContext":
        requested = self.permissions if permissions is None else frozenset(permissions)
        if not requested.issubset(self.permissions):
            missing = ", ".join(sorted(requested - self.permissions))
            raise PermissionError(
                f"Child context requests capabilities outside its parent authority: {missing}"
            )
        return replace(
            self,
            task_id=uuid4().hex,
            parent_task_id=self.task_id,
            node_id=node_id,
            permissions=requested,
            metadata=dict(self.metadata),
        )

    def new_task(self) -> "TaskExecutionContext":
        """Start a new task identity while preserving runtime ownership."""

        return replace(
            self,
            task_id=uuid4().hex,
            parent_task_id=None,
            node_id=None,
            metadata=dict(self.metadata),
        )

    def emit(self, event_type: str, data: Optional[Dict[str, Any]] = None) -> None:
        correlated = {
            "task_id": self.task_id,
            "parent_task_id": self.parent_task_id,
            "node_id": self.node_id,
            **(data or {}),
        }
        self.event_sink.emit(event_type, correlated)

    def record_metric(self, metric_type: str, data: Optional[Dict[str, Any]] = None) -> None:
        self.metrics_sink.record(
            {
                "metric_type": metric_type,
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
        return ledger.reserve_model_call(token_allowance or 0)

    def reservation_for_model_call(self, call_number: int) -> int:
        ledger = self.budget_ledger
        if ledger is None:
            raise RuntimeError("Ledger de tarefa nao inicializado.")
        return ledger.reservation_for(call_number)

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
        return ledger.reserve_tool_call()

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


# Compatibility name retained for callers that describe a task result. The
# runtime has one terminal-status owner; graph, step, validation, and tracker
# states remain separate subdomain state machines.
TaskStatus = OperationalStatus


@dataclass(frozen=True)
class Artifact:
    kind: str
    path: Optional[str] = None
    content: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TaskResult:
    status: TaskStatus
    summary: str = ""
    artifacts: tuple[Artifact, ...] = ()
    diagnostics: tuple[Dict[str, Any], ...] = ()
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == TaskStatus.SUCCEEDED

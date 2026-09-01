from types import SimpleNamespace

import pytest

from agent.cancellation import CancellationToken
from agent.orchestration.operations import OrchestratorOperations
from agent.orchestration.task_runner import TaskRunner
from agent.reporting.metrics import project_run_metrics
from agent.reporting.run_snapshot import build_canonical_run_snapshot
from agent.runtime.budget import TaskBudgetLedger
from agent.runtime.context import TaskExecutionContext
from agent.runtime.correlation import RunCorrelation
from agent.runtime.event_dispatch import RuntimeEventDispatcher
from agent.runtime.events import (
    RESERVED_EVENT_IDENTITY_FIELDS,
    RuntimeEvent,
    deserialize_runtime_event,
)
from agent.runtime.task_execution_context import TaskExecutionOwnershipMixin
from agent.state import AgentState
from agent.task_definition.models import TaskDefinitionRef
from agent.tools.contracts import (
    ToolDescriptor,
    ToolInvocation,
    ToolInvocationRequest,
    ToolResult,
    ToolStatus,
)
from agent.tools.invocation_gateway import ToolInvocationGateway
from agent.tools.tool_registry import ToolRegistry


class _Owner(TaskExecutionOwnershipMixin):
    def __init__(self, checkpoint: dict[str, object] | None = None) -> None:
        self.session = SimpleNamespace(
            messages=[], config={}, gateway=SimpleNamespace(), run_correlation=None
        )
        self.agent_state = AgentState()
        self.cancellation_token = CancellationToken()
        self.task_budget = TaskBudgetLedger()
        self._run_correlation = None
        self._run_id = None
        self._task_execution_context = None
        self._planning_context = None
        self._task_failed = False
        self._cancelled = False
        self._preserve_checkpoint = False
        self._task_start_time = 0.0
        self._run_metric_recorded = False
        self._metrics_start_line = 0
        self.event_dispatcher = RuntimeEventDispatcher(state=self.agent_state)
        self.workspace = SimpleNamespace(
            rollback=lambda: True,
            restore_points={},
            created_files=set(),
            discard_transactions=lambda: None,
        )
        self.context_manager = SimpleNamespace(
            _cached_project_context=None,
            maybe_compress_context=lambda: None,
        )
        self.task_definition_compiler = SimpleNamespace(
            last_ref=None,
            compile=lambda _task_id, _objective: None,
            resume=lambda _task_id, reference: reference,
        )
        self.task_context_resolver = SimpleNamespace(resolve=lambda _reference: object())
        self._checkpoint = checkpoint

    def _load_checkpoint(self):
        return self._checkpoint

    def _delete_checkpoint(self) -> None:
        pass

    def _save_checkpoint(self) -> bool:
        return True

    def _persist_memory_to_file(self) -> None:
        pass

    def _count_metrics_lines(self) -> int:
        return 0


def _checkpoint(*, terminal: str | None = None) -> tuple[dict[str, object], str]:
    state = AgentState(root_task_id="root-authoritative")
    state.objective = "resume objective"
    state.task_definition_ref = TaskDefinitionRef(
        task_id="root-authoritative",
        contract_version=1,
        contract_digest="0" * 64,
        spec_version=1,
        spec_digest="1" * 64,
        definition_state="complete",
    )
    if terminal is not None:
        state.terminal_disposition = terminal
        state.last_result = {
            "status": terminal,
            "error_code": "CANCELLED",
            "message": "terminal result",
            "executed": False,
        }
    return state.to_checkpoint_dict(), "root-authoritative"


def test_actual_task_runner_nonterminal_resume_binds_root_context_and_child_lineage() -> None:
    checkpoint, root_task_id = _checkpoint()
    owner = _Owner(checkpoint)
    observed: list[tuple[RunCorrelation, TaskExecutionContext]] = []
    runner = TaskRunner(owner)

    def execute(*_args, **_kwargs) -> str:
        observed.append((owner.run_correlation, owner.task_execution_context))
        owner.agent_state.terminal_disposition = "complete"
        return "resumed"

    runner._execute = execute  # type: ignore[method-assign]

    assert runner.run(None, None) == "resumed"
    correlation, context = observed[0]
    child = context.child("child-node")
    assert correlation is context.correlation
    assert correlation.root_task_id == root_task_id
    assert correlation.run_id != root_task_id
    assert child.run_id == correlation.run_id
    assert child.root_task_id == root_task_id
    assert child.parent_task_id == context.task_id
    assert child.node_id == "child-node"


def test_actual_terminal_resume_starts_new_run_and_preserves_authoritative_root() -> None:
    checkpoint, root_task_id = _checkpoint(terminal="cancelled")
    owner = _Owner(checkpoint)
    runner = TaskRunner(owner)
    executed: list[bool] = []
    runner._execute = lambda *_args, **_kwargs: executed.append(True)  # type: ignore[method-assign]

    assert "cancelada" in runner.run(None, None).casefold()
    assert executed == []
    assert owner.run_correlation.run_id != root_task_id
    assert owner.run_correlation.root_task_id == root_task_id
    assert owner.run_correlation.task_id == root_task_id
    assert owner.task_execution_context.correlation is owner.run_correlation


def test_preparation_failure_already_has_runtime_owned_correlation() -> None:
    owner = _Owner()
    owner._reset_task_state = lambda _objective: (_ for _ in ()).throw(RuntimeError("prepare"))  # type: ignore[method-assign]
    runner = TaskRunner(owner)
    runner._cleanup = lambda _count: None  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="prepare"):
        runner.run("nontrivial objective requiring work", None)

    assert isinstance(owner._run_correlation, RunCorrelation)
    assert owner.agent_state.runtime_correlation is owner._run_correlation


def test_reporting_component_cannot_create_runtime_identity() -> None:
    owner = SimpleNamespace(agent_state=AgentState())

    with pytest.raises(RuntimeError, match="runtime-owned correlation"):
        build_canonical_run_snapshot(owner, "failed", metrics=project_run_metrics([]))

    assert not hasattr(owner, "_run_correlation")


@pytest.mark.parametrize("reserved", sorted(RESERVED_EVENT_IDENTITY_FIELDS))
def test_conflicting_reserved_event_identity_fails_closed(reserved: str) -> None:
    correlation = RunCorrelation.fresh()
    canonical = {
        "run_id": correlation.run_id,
        "root_task_id": correlation.root_task_id,
        "task_id": correlation.task_id,
        "parent_task_id": correlation.parent_task_id,
        "node_id": correlation.node_id,
        "plan_id": "plan-right",
        "step_id": "step-right",
        "invocation_id": "inv-right",
    }
    data = {reserved: "wrong"}

    with pytest.raises(ValueError, match=reserved):
        RuntimeEvent.from_fields(
            "tool_start",
            correlation,
            data,
            plan_id=canonical["plan_id"],
            step_id=canonical["step_id"],
            invocation_id=canonical["invocation_id"],
        )


def test_legacy_event_projection_uses_only_exact_canonical_identities() -> None:
    correlation = RunCorrelation.fresh().child("node-a")
    event = RuntimeEvent.from_fields(
        "tool_end",
        correlation,
        {"plan_id": "plan-a", "step_id": "step-a", "invocation_id": "inv-a"},
    )

    projected = event.to_legacy_dict()

    for name in RESERVED_EVENT_IDENTITY_FIELDS:
        if projected[name] is not None:
            assert projected["data"][name] == projected[name]
    assert not RESERVED_EVENT_IDENTITY_FIELDS.intersection(event.data)


def test_child_tool_invocation_preserves_lineage_events_and_state_observation() -> None:
    class Adapter:
        def descriptors(self) -> tuple[ToolDescriptor, ...]:
            return (ToolDescriptor("echo", "echo"),)

        def invoke(self, invocation: ToolInvocation) -> ToolResult:
            return ToolResult(
                invocation.invocation_id,
                ToolStatus.SUCCEEDED,
                data={"value": "ok"},
                executed=True,
            )

    root = RunCorrelation.fresh()
    context = TaskExecutionContext(
        model_gateway=SimpleNamespace(),
        cancellation=CancellationToken(),
        correlation=root,
    )
    child = context.child("child-tool")
    state = AgentState(root_task_id=root.root_task_id)
    state.runtime_correlation = root
    dispatcher = RuntimeEventDispatcher(state=state)
    registry = ToolRegistry()
    registry.register_adapter(Adapter())
    gateway = ToolInvocationGateway(
        registry,
        event_dispatcher=dispatcher,
        correlation_provider=lambda: root,
        correlated_state_recorder=lambda name, args, result, lineage: state.record_tool_result(
            name, args, result, correlation=lineage
        ),
    )

    success = gateway.run(child.tool_invocation_request("inv-ok", "echo"))
    denied = gateway.run(child.tool_invocation_request("inv-denied", "missing"))

    assert success.status is ToolStatus.SUCCEEDED
    assert denied.status is ToolStatus.UNAVAILABLE
    assert [event["type"] for event in state.events] == [
        "tool_start",
        "tool_end",
        "tool_denied",
    ]
    assert [event["invocation_id"] for event in state.events] == [
        "inv-ok",
        "inv-ok",
        "inv-denied",
    ]
    for event in state.events:
        assert event["run_id"] == root.run_id
        assert event["root_task_id"] == root.root_task_id
        assert event["task_id"] == child.task_id
        assert event["parent_task_id"] == root.task_id
        assert event["node_id"] == "child-tool"
    for entry in state.tool_history:
        assert entry["run_id"] == root.run_id
        assert entry["root_task_id"] == root.root_task_id
        assert entry["task_id"] == child.task_id
        assert entry["parent_task_id"] == root.task_id
        assert entry["node_id"] == "child-tool"


def test_partial_task_lineage_fails_closed_when_runtime_correlation_is_active() -> None:
    calls: list[str] = []

    class Adapter:
        def descriptors(self) -> tuple[ToolDescriptor, ...]:
            return (ToolDescriptor("echo", "echo"),)

        def invoke(self, invocation: ToolInvocation) -> ToolResult:
            calls.append(invocation.invocation_id)
            return ToolResult(invocation.invocation_id, ToolStatus.SUCCEEDED, executed=True)

    root = RunCorrelation.fresh()
    state = AgentState(root_task_id=root.root_task_id)
    state.runtime_correlation = root
    registry = ToolRegistry()
    registry.register_adapter(Adapter())
    gateway = ToolInvocationGateway(
        registry,
        event_dispatcher=RuntimeEventDispatcher(state=state),
        correlation_provider=lambda: root,
        correlated_state_recorder=lambda name, args, result, lineage: state.record_tool_result(
            name, args, result, correlation=lineage
        ),
    )

    result = gateway.run(
        ToolInvocationRequest(
            "inv-partial",
            "echo",
            task_id="child-without-lineage",
        )
    )

    assert result.status is ToolStatus.PROTOCOL_ERROR
    assert result.error is not None
    assert result.error.code == "RUNTIME_LINEAGE_INVALID"
    assert calls == []
    assert [event["type"] for event in state.events] == ["tool_denied"]


def test_checkpoint_events_are_canonical_and_round_trip_all_identity_facts() -> None:
    correlation = RunCorrelation.fresh()
    state = AgentState(root_task_id=correlation.root_task_id)
    state.runtime_correlation = correlation
    dispatcher = RuntimeEventDispatcher(state=state)
    owner = SimpleNamespace(
        agent_state=state,
        event_dispatcher=dispatcher,
        _ensure_run_correlation=lambda: correlation,
        verbose=False,
    )
    owner.tool_invocation_gateway = SimpleNamespace(
        are_invocations_quiescent=lambda **_kwargs: False
    )
    owner.checkpoint_manager = SimpleNamespace(save=lambda _state: True)

    assert OrchestratorOperations._save_checkpoint(owner) is False
    owner.tool_invocation_gateway = SimpleNamespace(
        are_invocations_quiescent=lambda **_kwargs: True
    )
    owner.checkpoint_manager = SimpleNamespace(save=lambda _state: False)
    assert OrchestratorOperations._save_checkpoint(owner) is False
    assert [event["type"] for event in state.events] == [
        "checkpoint_deferred",
        "checkpoint_persistence_failed",
    ]

    restored = AgentState()
    restored.from_checkpoint_dict(state.to_checkpoint_dict())
    round_tripped = [deserialize_runtime_event(event) for event in restored.events]
    for event in round_tripped:
        assert event.run_id == correlation.run_id
        assert event.root_task_id == correlation.root_task_id
        assert event.task_id == correlation.task_id


def test_checkpoint_save_requires_explicit_true_confirmation() -> None:
    correlation = RunCorrelation.fresh()
    state = AgentState(root_task_id=correlation.root_task_id)
    state.runtime_correlation = correlation
    owner = SimpleNamespace(
        agent_state=state,
        event_dispatcher=RuntimeEventDispatcher(state=state),
        _ensure_run_correlation=lambda: correlation,
        verbose=False,
        tool_invocation_gateway=SimpleNamespace(
            are_invocations_quiescent=lambda **_kwargs: True
        ),
        checkpoint_manager=SimpleNamespace(save=lambda _state: None),
    )

    assert OrchestratorOperations._save_checkpoint(owner) is False
    assert [event["type"] for event in state.events] == [
        "checkpoint_persistence_failed"
    ]


def test_checkpoint_events_require_the_canonical_dispatcher() -> None:
    correlation = RunCorrelation.fresh()
    state = AgentState(root_task_id=correlation.root_task_id)
    state.runtime_correlation = correlation
    owner = SimpleNamespace(
        agent_state=state,
        event_dispatcher=None,
        _ensure_run_correlation=lambda: correlation,
        verbose=False,
    )

    with pytest.raises(RuntimeError, match="canonical runtime event dispatcher"):
        OrchestratorOperations._emit_checkpoint_event(
            owner,
            "checkpoint_deferred",
            {"reason": "test"},
        )

    assert state.events == []

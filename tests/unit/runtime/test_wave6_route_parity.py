from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent.cancellation import CancellationToken
from agent.llm.contracts import ProviderCapabilities
from agent.orchestration.security_service import SecurityAnalysisService
from agent.planning.plan_executor import PlanExecutor
from agent.planning.reactive_loop import ReactiveLoop
from agent.planning.task_graph import TaskGraph, TaskNode
from agent.planning.task_scheduler import TaskGraphScheduler
from agent.runtime.context import RuntimeLimits, TaskExecutionContext, TaskResult, TaskStatus
from agent.runtime.model_call import ModelCallService
from agent.runtime.task_policy import TaskPolicyDecision, TaskPolicyError, TaskPolicyResult, TaskRuntimePolicy


class _Gateway:
    provider_name = "fake"
    capabilities = ProviderCapabilities()


def _context(*, max_steps: int = 10, max_model_calls: int = 10) -> TaskExecutionContext:
    return TaskExecutionContext(
        model_gateway=_Gateway(),
        cancellation=CancellationToken(),
        limits=RuntimeLimits(
            max_steps=max_steps,
            max_model_calls=max_model_calls,
            max_task_tool_calls=10,
            max_task_tokens=10_000,
            max_task_wall_seconds=100,
        ),
        permissions=frozenset({"read", "write"}),
    )


def test_plan_parallel_route_dispatches_only_the_admitted_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    context = _context(max_steps=1)
    orchestrator = SimpleNamespace(
        task_policy=context.task_policy,
        agent_state=SimpleNamespace(tool_history=[]),
        session=SimpleNamespace(config={}),
        _task_start_time=0.0,
    )
    executor = PlanExecutor(orchestrator, step_executor=SimpleNamespace())
    dispatched: list[list[int]] = []

    def run_parallel(indices: list[int]):
        dispatched.append(indices)
        return {}, {}, {}

    monkeypatch.setattr(executor, "_run_parallel_tools", run_parallel)
    monkeypatch.setattr(
        executor,
        "_finalize_parallel",
        lambda *args: SimpleNamespace(answer=None, stop=False),
    )

    executor._execute_parallel_read_batch([0, 1], "objective", {})

    assert dispatched == [[0]]
    assert context.task_policy.logical_work_units_consumed == 1


def test_graph_route_bounds_a_parallel_batch_before_pool_submission() -> None:
    context = _context(max_steps=2)
    context.task_policy.admit_work_units()
    calls: list[str] = []

    class _Executor:
        def execute(self, node: TaskNode, child: TaskExecutionContext) -> TaskResult:
            calls.append(node.node_id)
            return TaskResult(TaskStatus.SUCCEEDED, summary=node.node_id)

    graph = TaskGraph("objective", (TaskNode("a", "a"), TaskNode("b", "b")))
    result = TaskGraphScheduler(_Executor(), max_workers=2).execute(graph, context)

    assert calls == ["a"]
    assert result.states["a"].value == "succeeded"
    assert result.states["b"].value == "blocked"


def test_security_route_passes_root_cancellation_to_direct_gateway() -> None:
    token = CancellationToken()
    context = _context()
    policy = context.task_policy
    gateway_calls: list[dict[str, object]] = []

    class _SecurityGateway:
        def run(self, *args, **kwargs):
            del args
            gateway_calls.append(kwargs)
            return {"ok": True, "status": "succeeded", "data": {}}

    orchestrator = SimpleNamespace(
        task_policy=TaskRuntimePolicy(
            context.limits,
            state=policy.state,
            budget_ledger=policy.budget_ledger,
            cancellation=token,
        ),
        cancellation_token=token,
        agent_state=SimpleNamespace(tool_history=[]),
        session=SimpleNamespace(config={}),
        tool_invocation_gateway=_SecurityGateway(),
    )
    service = SecurityAnalysisService(orchestrator)
    service._target_file = lambda objective: "target.py"
    service._answer_without_findings = lambda *args: "safe"

    result = service.run("scan target")

    assert result.route == "security"
    assert result.disposition.value == "handled"
    assert gateway_calls[0]["cancellation_token"] is token
    assert orchestrator.task_policy.logical_work_units_consumed == 1


def test_model_route_denies_before_measurement_or_provider_when_budget_is_exhausted() -> None:
    context = _context(max_model_calls=1)
    context.budget_ledger.reserve_model_call()
    service = ModelCallService(context)

    with pytest.raises(TaskPolicyError) as caught:
        service._admit(object())

    assert caught.value.result.decision is TaskPolicyDecision.QUANTITATIVE_EXHAUSTED


def test_reactive_route_consults_the_same_policy_seam() -> None:
    calls: list[dict[str, object]] = []

    class _Policy:
        active_elapsed_seconds = 0.0

        def check_current(self, **kwargs):
            calls.append(kwargs)
            return TaskPolicyResult(TaskPolicyDecision.ALLOW)

    orchestrator = SimpleNamespace(
        task_policy=_Policy(),
        agent_state=SimpleNamespace(tool_history=[]),
        session=SimpleNamespace(config={}),
    )

    assert ReactiveLoop(orchestrator)._limit_answer("objective", 1) is None
    assert calls == [{"watchdog_reason": None}]

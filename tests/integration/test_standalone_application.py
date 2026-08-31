import hashlib
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path

import pytest

from agent.application import AgentApplication
from agent.approval import ApprovalDecision, ApprovalRequest, AutoApprove
from agent.llm.contracts import ModelRequest, ModelResponse
from agent.llm.decision_contract import ModelRequestContract
from agent.memory.memory import MemoryLoadError
from agent.orchestration.task_runner import TaskRunner
from agent.planning.step_policies import StepPolicies
from agent.planning.task_completion import (
    allow_linear_completion,
    initialize_task_progression,
)
from agent.runtime.config_errors import ConfigNotFound
from agent.runtime.config_repository import ConfigRepository
from agent.runtime.instance_lock import InstanceLockError
from agent.runtime.paths import AppPaths
from agent.runtime.workspace_context import WorkspaceContext
from agent.skills import load_skill_registry
from agent.tools.authority import OperationalMode
from agent.tools.builtin_adapter import BuiltinToolAdapter
from agent.tools.contracts import (
    CancellationSafetyMode,
    ToolDescriptor,
    ToolInvocation,
    ToolInvocationRequest,
    ToolResult,
    ToolStatus,
)
from agent.tools.extension_bootstrap import ApplicationExtensionBootstrap
from agent.tools.extension_catalog_service import ExtensionCatalogService
from agent.tools.extension_catalog_storage import ExtensionCatalogStorage
from agent.tools.invocation_execution import InvocationLivenessError
from agent.tools.tool_registry import ToolRegistry
from agent.tools.workspace_extensions_service import WorkspaceExtensionService
from agent.watchdog import Watchdog
from tests.support.offline_scenarios import OfflineLegacyGateway, OfflineModelGateway
from tests.support.task_definition import task_definition_response


def _initialized_paths(tmp_path: Path) -> AppPaths:
    paths = AppPaths.discover(tmp_path / "home", env={})
    ConfigRepository(paths).initialize()
    return paths


class _QueuedLegacyGateway(OfflineLegacyGateway):
    def __init__(self, responses: list[str]) -> None:
        OfflineModelGateway.__init__(self, responses)
        self.payloads = []

    def complete(self, request: ModelRequest):
        request_contract = getattr(request.request_contract, "value", request.request_contract)
        if request_contract in {
            ModelRequestContract.TASK_CONTRACT.value,
            ModelRequestContract.TASK_SPEC.value,
        }:
            authority = task_definition_response(self, request)
            if authority is None:
                raise AssertionError("task-definition request was not handled")
            return ModelResponse(content=authority)
        if request.request_contract is ModelRequestContract.TOOL_DISCOVERY:
            marker = "<untrusted_tool_catalog>"
            end_marker = "</untrusted_tool_catalog>"
            content = request.messages[-1].content
            try:
                catalog_text = content.split(marker, 1)[1].split(end_marker, 1)[0]
                catalog = json.loads(catalog_text.strip())
                names = [entry["name"] for entry in catalog if isinstance(entry, dict)]
            except (IndexError, KeyError, TypeError, json.JSONDecodeError):
                names = []
            return ModelResponse(content=json.dumps({"tools": names[:8]}))
        payload = {
            "model": request.model,
            "messages": [
                {"role": message.role, "content": message.content}
                for message in request.messages
            ],
            "max_tokens": request.max_output_tokens,
            "stream": request.stream,
        }
        if request.structured_output is not None:
            if request.structured_output.grammar is not None:
                payload["grammar"] = request.structured_output.grammar
        self.payloads.append(payload)
        return super().complete(request)


class _CountingApproval:
    def __init__(self) -> None:
        self.requests: list[ApprovalRequest] = []

    def request(self, request: ApprovalRequest) -> ApprovalDecision:
        self.requests.append(request)
        return ApprovalDecision.APPROVED


class _TaskRunnerLivenessWriter:
    def __init__(self, started: threading.Event, release: threading.Event) -> None:
        self.started = started
        self.release = release

    def descriptors(self):
        return (
            ToolDescriptor(
                "task_runner_liveness_writer",
                "writer",
                capabilities=frozenset({"write"}),
                cancellation_safety=CancellationSafetyMode.BOUNDED_COOPERATIVE,
            ),
        )

    def invoke(self, invocation: ToolInvocation) -> ToolResult:
        self.started.set()
        self.release.wait(timeout=5)
        return ToolResult(invocation.invocation_id, ToolStatus.SUCCEEDED, executed=True)


def _invoke_task_runner_mutator(gateway, errors: list[BaseException]) -> None:
    try:
        gateway.run(
            ToolInvocationRequest(
                "task-runner-liveness",
                "task_runner_liveness_writer",
                timeout_seconds=30,
            )
        )
    except BaseException as exc:
        errors.append(exc)


def _interrupting_task_runner_execute(
    runner,
    inputs,
    on_chunk,
    *,
    target_orchestrator,
    original_execute,
    started: threading.Event,
    gateway,
    errors: list[BaseException],
    mutator_threads: list[threading.Thread],
):
    if runner.orchestrator is not target_orchestrator:
        return original_execute(runner, inputs, on_chunk)
    thread = threading.Thread(
        target=_invoke_task_runner_mutator,
        args=(gateway, errors),
        daemon=True,
    )
    mutator_threads.append(thread)
    thread.start()
    assert started.wait(timeout=2)
    raise KeyboardInterrupt


def test_application_does_not_publish_or_close_while_mutator_is_alive(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    paths = _initialized_paths(tmp_path)
    started = threading.Event()
    release = threading.Event()

    class ViolatingWriter:
        def descriptors(self):
            return (
                ToolDescriptor(
                    "application_liveness_writer",
                    "writer",
                    capabilities=frozenset({"write"}),
                    cancellation_safety=CancellationSafetyMode.BOUNDED_COOPERATIVE,
                ),
            )

        def invoke(self, invocation: ToolInvocation) -> ToolResult:
            started.set()
            release.wait(timeout=5)
            return ToolResult(invocation.invocation_id, ToolStatus.SUCCEEDED, executed=True)

    application = AgentApplication.create(
        paths=paths,
        workspace=workspace,
        approval_policy=AutoApprove(),
        configure_logging=False,
    )
    registry = ToolRegistry(runtime_identity=application.tool_registry.runtime_identity)
    registry.register_adapter(ViolatingWriter())
    registry.freeze()
    application.tool_invocation_gateway.registry = registry
    original_drain = application.tool_invocation_gateway.drain_invocations

    def invoke_mutator(_objective: str | None, stream_callback=None):
        del stream_callback
        return application.tool_invocation_gateway.run(
            ToolInvocationRequest(
                "application-liveness",
                "application_liveness_writer",
                timeout_seconds=1,
            )
        )

    monkeypatch.setattr(application.orchestrator, "run", invoke_mutator)
    monkeypatch.setattr(
        "agent.tools.invocation_quiescence.CANCELLATION_GRACE_SECONDS", 0.05
    )

    try:
        with pytest.raises(InvocationLivenessError):
            application.run("invoke the mutator")
        assert started.is_set()
        assert application.orchestrator.agent_state.terminal_disposition is None
        assert application._closed is False

        monkeypatch.setattr(
            application.tool_invocation_gateway,
            "drain_invocations",
            lambda **_kwargs: False,
        )
        with pytest.raises(InvocationLivenessError):
            application.close()
        assert application._closed is False

        monkeypatch.setattr(
            application.tool_invocation_gateway,
            "drain_invocations",
            original_drain,
        )
        release.set()
        assert original_drain(timeout_seconds=2) is True
        application.close()
        assert application._closed is True
    finally:
        release.set()
        if not application._closed:
            monkeypatch.setattr(
                application.tool_invocation_gateway,
                "drain_invocations",
                original_drain,
            )
            application.tool_invocation_gateway.drain_invocations(timeout_seconds=2)
            application.close()


def test_real_task_runner_defers_interrupt_terminal_and_cleanup_until_quiescent(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    paths = _initialized_paths(tmp_path)
    started = threading.Event()
    release = threading.Event()
    mutator_threads: list[threading.Thread] = []
    mutator_errors: list[BaseException] = []
    cleanup_while_active: list[bool] = []

    application = AgentApplication.create(
        paths=paths,
        workspace=workspace,
        approval_policy=AutoApprove(),
        configure_logging=False,
    )
    registry = ToolRegistry(runtime_identity=application.tool_registry.runtime_identity)
    registry.register_adapter(_TaskRunnerLivenessWriter(started, release))
    registry.freeze()
    gateway = application.tool_invocation_gateway
    gateway.registry = registry
    original_drain = gateway.drain_invocations

    def bounded_test_drain(**_kwargs):
        return original_drain(timeout_seconds=0.05)

    original_persist = application.orchestrator._persist_memory_to_file

    def track_persist() -> None:
        cleanup_while_active.append(not release.is_set())
        original_persist()

    original_execute = TaskRunner._execute
    monkeypatch.setattr(
        TaskRunner,
        "_execute",
        partial(
            _interrupting_task_runner_execute,
            target_orchestrator=application.orchestrator,
            original_execute=original_execute,
            started=started,
            gateway=gateway,
            errors=mutator_errors,
            mutator_threads=mutator_threads,
        ),
    )
    monkeypatch.setattr(gateway, "drain_invocations", bounded_test_drain)
    monkeypatch.setattr(application.orchestrator, "_persist_memory_to_file", track_persist)

    try:
        with pytest.raises(InvocationLivenessError):
            application.run("interrupt while the mutator is active")
        assert application.orchestrator.agent_state.terminal_disposition is None
        assert application.orchestrator._cancelled is False
        assert cleanup_while_active == []
    finally:
        release.set()
        for mutator_thread in mutator_threads:
            mutator_thread.join(timeout=3)
            assert not mutator_thread.is_alive()
        assert mutator_errors == []
        monkeypatch.setattr(gateway, "drain_invocations", original_drain)
        if not application._closed:
            application.close()


def _run_queued_task(
    tmp_path: Path,
    content: str,
    objective: str,
    responses: list[str],
    mode: OperationalMode = OperationalMode.EDITOR,
    extra_files: dict[str, str] | None = None,
    approval_policy=None,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "controle.txt").write_text(content, encoding="utf-8")
    for relative_path, extra_content in (extra_files or {}).items():
        (workspace / relative_path).write_text(extra_content, encoding="utf-8")
    gateway = _QueuedLegacyGateway(responses)
    with AgentApplication.create(
        paths=_initialized_paths(tmp_path),
        workspace=workspace,
        gateway=gateway,
        approval_policy=approval_policy or AutoApprove(),
        operational_mode=mode,
        configure_logging=False,
    ) as application:
        result = application.run(objective)
        history = [entry["tool"] for entry in application.orchestrator.agent_state.tool_history]
        state = application.orchestrator.agent_state
        progression = {
            "events": list(state.events),
            "requested": list(state.requested_effects),
            "executed": list(state.executed_effects),
            "waived": list(state.waived_effects),
            "pending": list(state.pending_effects()),
            "continuations": state.continuation_attempts,
            "reasoning_turns": state.reasoning_turns_used,
            "terminal": state.terminal_disposition,
            "plan": [dict(step) for step in state.plan],
            "step_statuses": [state.get_step_status(index).value for index in range(len(state.plan))],
            "history_entries": [dict(entry) for entry in state.tool_history],
        }
    return result, gateway, workspace, history, progression


def _manual_deferred_plan() -> list[dict[str, object]]:
    return [
        {"tool": "file_reader", "args": {"file_path": "controle.txt"}},
        {
            "kind": "deferred_condition",
            "observation_ref": 1,
            "predicate": {"op": "equals", "value": "original"},
            "on_true": {
                "tool": "code_task",
                "args": {
                    "action": "modify",
                    "objective": "Altere controle.txt para modificado",
                    "targets": ["controle.txt"],
                },
            },
            "on_false": {"waive_effect": "write"},
        },
    ]


def test_result_binding_executes_exact_observed_value_without_replanning(tmp_path: Path) -> None:
    result, gateway, workspace, history, progression = _run_queued_task(
        tmp_path,
        "modificado",
        "Leia controle.txt e procure nos arquivos do workspace pela palavra que ele contém.",
        [
            '{"persona":"coder"}',
                '{"action":"use_tools","plan":[{"tool":"file_reader","args":{"file_path":"controle.txt"}},{"tool":"grep","args":{"path":"."},"bindings":{"pattern":{"from_step":1,"path":[]}}}]}',
                '{"action":"complete","reason":"a busca foi executada"}',
            "A busca encontrou a palavra observada.",
        ],
    )

    assert result.status == "succeeded"
    assert history == ["file_reader", "grep"]
    grep_entry = next(entry for entry in progression["history_entries"] if entry["tool"] == "grep")
    assert grep_entry["args"]["pattern"] == "modificado"
    assert "bindings" not in grep_entry["args"]
    assert len(gateway.payloads) == 4
    assert workspace.joinpath("controle.txt").read_text(encoding="utf-8") == "modificado"
    assert progression["continuations"] == 0


def test_phase3_real_correlation_chain_reaches_final_snapshot(tmp_path: Path) -> None:
    result, _gateway, _workspace, _history, progression = _run_queued_task(
        tmp_path,
        "phase3-observation",
        "Read controle.txt and report the observed value.",
        [
            '{"persona":"coder"}',
            '{"action":"use_tools","plan":[{"tool":"file_reader","args":{"file_path":"controle.txt"}}]}',
            '{"action":"complete","reason":"the observation is sufficient"}',
            "The observed value is phase3-observation.",
        ],
    )

    snapshot = result.snapshot
    assert snapshot is not None
    correlation = snapshot.correlation
    events = progression["events"]
    model_index = next(
        index for index, event in enumerate(events) if event["type"] == "model_call_started"
    )
    plan_index = next(
        index for index, event in enumerate(events) if event["type"] == "plan_created"
    )
    start_index = next(
        index for index, event in enumerate(events) if event["type"] == "tool_start"
    )
    end_index = next(
        index for index, event in enumerate(events) if event["type"] == "tool_end"
    )
    outcome_index = next(
        index for index, event in enumerate(events) if event["type"] == "task_outcome"
    )
    assert model_index < plan_index < start_index < end_index < outcome_index

    plan_event = events[plan_index]
    plan_id = plan_event["plan_id"]
    step_id = snapshot.projection_facts.canonical_plan[0]["_step_id"]
    start = events[start_index]
    end = events[end_index]
    invocation_id = start["invocation_id"]
    assert isinstance(plan_id, str) and plan_id
    assert end["invocation_id"] == invocation_id
    assert start["plan_id"] == plan_id
    assert end["plan_id"] == plan_id
    assert start["step_id"] == step_id
    assert end["step_id"] == step_id

    for event in (events[model_index], plan_event, start, end, events[outcome_index]):
        assert event["run_id"] == correlation.run_id
        assert event["root_task_id"] == correlation.root_task_id
    evidence = snapshot.projection_facts.invocation_evidence
    assert evidence[0]["invocation_id"] == invocation_id
    assert evidence[0]["plan_id"] == plan_id
    assert evidence[0]["step_id"] == step_id
    assert snapshot.tool_observation_refs[0]["invocation_id"] == invocation_id
    assert result.status == "succeeded"
    assert snapshot.status == "succeeded"


def test_invalid_provenance_gets_same_tool_binding_repair_and_executes(tmp_path: Path) -> None:
    result, gateway, workspace, history, progression = _run_queued_task(
        tmp_path,
        "workspace marker",
        "Leia fonte_h2.txt e procure nos outros arquivos do workspace pela palavra que ele contém.",
        [
            '{"persona":"coder"}',
            '{"action":"use_tools","plan":[{"tool":"file_reader","args":{"file_path":"fonte_h2.txt"}},{"tool":"grep","args":{"path":".","pattern":"${1.text}","recursive":true,"max_results":20}}]}',
            '{"tool":"grep","args":{"path":".","recursive":true,"max_results":20},"bindings":{"pattern":{"from_step":1,"path":[]}}}',
            '{"action":"complete","reason":"a busca foi executada"}',
            "A busca foi executada com a palavra observada.",
        ],
        extra_files={"fonte_h2.txt": "orion_584271", "other.txt": "orion_584271"},
    )

    assert result.status == "succeeded"
    assert history == ["file_reader", "grep"]
    grep_entry = next(entry for entry in progression["history_entries"] if entry["tool"] == "grep")
    assert grep_entry["args"]["pattern"] == "orion_584271"
    assert len(gateway.payloads) == 5
    assert "binding syntax" in gateway.payloads[1]["messages"][-1]["content"]
    assert "CONSTRAINED VALIDATION REPAIR" in gateway.payloads[2]["messages"][-1]["content"]
    assert "${...}" in gateway.payloads[2]["messages"][-1]["content"]
    assert workspace.joinpath("fonte_h2.txt").read_text(encoding="utf-8") == "orion_584271"


def test_parallel_read_batch_prepares_bound_consumer_after_producer(tmp_path: Path) -> None:
    result, gateway, _workspace, history, progression = _run_queued_task(
        tmp_path,
        "modificado",
        "Leia controle.txt e outro.txt e procure a palavra observada.",
        [
            '{"persona":"coder"}',
            '{"action":"use_tools","plan":[{"tool":"file_reader","args":{"file_path":"controle.txt"}},{"tool":"file_reader","args":{"file_path":"outro.txt"}},{"tool":"grep","args":{"path":"."},"bindings":{"pattern":{"from_step":1,"path":[]}}}]}',
            '{"action":"complete","reason":"as leituras e a busca foram executadas"}',
            "A busca usou o valor observado.",
        ],
        extra_files={"outro.txt": "independente"},
    )

    assert result.status == "succeeded"
    assert history.count("file_reader") == 2
    assert history[-1] == "grep"
    grep_entry = progression["history_entries"][-1]
    assert grep_entry["tool"] == "grep"
    assert grep_entry["args"]["pattern"] == "modificado"
    assert "bindings" not in grep_entry["args"]
    assert len(gateway.payloads) == 4


def test_bound_path_keeps_confinement_on_consumer_dispatch(tmp_path: Path) -> None:
    result, _gateway, workspace, history, progression = _run_queued_task(
        tmp_path,
        "modificado",
        "Leia controle.txt e use o resultado para continuar.",
        [
            '{"persona":"coder"}',
            '{"action":"use_tools","plan":[{"tool":"echo","args":{"message":"../outside.txt"}},{"tool":"directory_lister","args":{"path":"."}},{"tool":"file_reader","args":{},"bindings":{"file_path":{"from_step":1,"path":[]}}}]}',
        ],
    )

    assert result.status == "failed"
    assert history[-1] == "file_reader"
    assert progression["history_entries"][-1]["args"]["file_path"] == "../outside.txt"
    assert "outside.txt" not in {path.name for path in workspace.parent.iterdir()}


def test_reasoning_boundary_continues_once_after_prefix_exhaustion(tmp_path: Path) -> None:
    result, gateway, _workspace, history, progression = _run_queued_task(
        tmp_path,
        "modificado",
        "Leia controle.txt e decida semanticamente se há uma ação adicional necessária.",
        [
            '{"persona":"coder"}',
            '{"action":"continue_after_plan","plan":[{"tool":"file_reader","args":{"file_path":"controle.txt"}}]}',
            '{"action":"complete","reason":"a observação basta"}',
            "A observação foi suficiente; nenhuma ação adicional foi executada.",
        ],
    )

    assert result.status == "succeeded"
    assert history == ["file_reader"]
    assert progression["reasoning_turns"] == 1
    assert len(gateway.payloads) == 4
    assert progression["terminal"] == "complete"


def test_reasoning_boundary_allows_two_bounded_phases(tmp_path: Path) -> None:
    result, gateway, _workspace, history, progression = _run_queued_task(
        tmp_path,
        "modificado",
        "Leia o arquivo e depois faça duas fases de raciocínio antes de responder.",
        [
            '{"persona":"coder"}',
            '{"action":"continue_after_plan","plan":[{"tool":"file_reader","args":{"file_path":"controle.txt"}}]}',
            '{"action":"execute","plan":[{"tool":"directory_lister","args":{"path":"."}}]}',
            '{"action":"complete","reason":"as duas observações bastam"}',
            "As duas fases foram concluídas com as observações reais.",
        ],
    )

    assert result.status == "succeeded"
    assert history == ["file_reader", "directory_lister"]
    assert progression["reasoning_turns"] == 2
    assert progression["terminal"] == "complete"
    assert len(gateway.payloads) == 5


def _planner_deferred_response() -> str:
    return json.dumps(
        {
            "action": "use_tools",
            "plan": _manual_deferred_plan(),
        }
    )


def _run_manual_deferred_task(
    tmp_path: Path,
    content: str,
    responses: list[str],
):
    objective = (
        'Se controle.txt contiver exatamente "original", altere para "modificado"; '
        "caso contrario nao altere."
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "controle.txt").write_text(content, encoding="utf-8")
    gateway = _QueuedLegacyGateway(responses)
    approval = _CountingApproval()
    with AgentApplication.create(
        paths=_initialized_paths(tmp_path),
        workspace=workspace,
        gateway=gateway,
        approval_policy=approval,
        operational_mode=OperationalMode.EDITOR,
        configure_logging=False,
    ) as application:
        orchestrator = application.orchestrator
        orchestrator._reset_task_state(objective)
        initialize_task_progression(orchestrator, objective)
        orchestrator._task_start_time = Watchdog.start_task()
        orchestrator._route_persona(objective)
        execution = orchestrator.execution_gateway.execute_validated_plan(
            _manual_deferred_plan(),
            objective,
            {},
        )
        completion_blocker = allow_linear_completion(orchestrator, objective)
        state = orchestrator.agent_state
        snapshot = {
            "history": [item["tool"] for item in state.tool_history],
            "events": list(state.events),
            "requested": list(state.requested_effects),
            "executed": list(state.executed_effects),
            "waived": list(state.waived_effects),
            "pending": list(state.pending_effects()),
            "continuations": state.continuation_attempts,
            "terminal": state.terminal_disposition,
            "step_statuses": [
                state.get_step_status(index).value for index in range(len(state.plan))
            ],
        }
    return (
        execution,
        completion_blocker,
        gateway,
        approval,
        workspace,
        snapshot,
    )


def test_manual_deferred_equals_true_promotes_only_write_branch(tmp_path: Path) -> None:
    execution, blocker, gateway, approval, workspace, snapshot = (
        _run_manual_deferred_task(
            tmp_path,
            "original",
            [
                '{"persona":"coder"}',
                '{"changes":[{"path":"controle.txt","kind":"edit","edits":[{"operation":"replace","start_line":1,"end_line":1,"content":"modificado"}]}]}',
            ],
        )
    )

    assert execution.aborted is False
    assert blocker is not None
    assert workspace.joinpath("controle.txt").read_text(encoding="utf-8") == "modificado"
    assert snapshot["history"] == ["file_reader", "code_task"]
    assert snapshot["executed"] == ["write"]
    assert snapshot["waived"] == []
    assert snapshot["pending"] == []
    assert snapshot["continuations"] == 0
    assert snapshot["terminal"] == "unverified"
    assert snapshot["step_statuses"][-1] == "unverified"
    assert approval.requests
    assert len(gateway.payloads) == 2  # router plus the canonical code proposal
    assert len(gateway.calls) == 2
    resolved = next(
        event
        for event in snapshot["events"]
        if event.get("type") == "deferred_condition_resolved"
    )
    assert resolved["data"]["selected_branch"] == "true"
    assert not any(event.get("type") == "replan" for event in snapshot["events"])


def test_manual_deferred_equals_false_binds_existing_write_waiver(tmp_path: Path) -> None:
    execution, blocker, gateway, approval, workspace, snapshot = (
        _run_manual_deferred_task(
            tmp_path,
            "modificado",
            ['{"persona":"coder"}', '{"action":"complete","reason":"a dispensa foi registrada"}'],
        )
    )

    assert execution.aborted is False
    assert blocker is None
    assert workspace.joinpath("controle.txt").read_text(encoding="utf-8") == "modificado"
    assert snapshot["history"] == ["file_reader"]
    assert snapshot["executed"] == []
    assert snapshot["waived"] == ["write"]
    assert snapshot["pending"] == []
    assert snapshot["continuations"] == 0
    assert approval.requests == []
    assert len(gateway.payloads) == 2
    assert len(gateway.calls) == 2
    resolved = next(
        event
        for event in snapshot["events"]
        if event.get("type") == "deferred_condition_resolved"
    )
    assert resolved["data"]["selected_branch"] == "false"
    waiver = next(
        event
        for event in snapshot["events"]
        if event.get("type") == "effect_waiver_bound"
    )
    assert waiver["data"]["source"] == "deferred_condition"
    assert not any(event.get("type") == "replan" for event in snapshot["events"])


def test_manual_deferred_blocks_when_file_observation_is_only_a_summary(
    tmp_path: Path,
) -> None:
    execution, blocker, gateway, approval, workspace, snapshot = (
        _run_manual_deferred_task(
            tmp_path,
            "x" * 25_000,
            ['{"persona":"coder"}'],
        )
    )

    assert execution.aborted is False
    assert blocker == "deferred_condition_blocked"
    assert workspace.joinpath("controle.txt").read_text(encoding="utf-8") == "x" * 25_000
    assert snapshot["history"] == ["file_reader"]
    assert snapshot["executed"] == []
    assert snapshot["waived"] == []
    assert snapshot["pending"] == ["write"]
    assert approval.requests == []
    assert len(gateway.payloads) == 1
    assert len(gateway.calls) == 1
    blocked = next(
        event
        for event in snapshot["events"]
        if event.get("type") == "deferred_condition_blocked"
    )
    assert "integral" in blocked["data"]["reason"]


def test_real_initial_planner_deferred_true_promotes_write_without_continuation(
    tmp_path: Path,
) -> None:
    approval = _CountingApproval()
    result, gateway, workspace, history, progression = _run_queued_task(
        tmp_path,
        "original",
        'Se controle.txt contiver exatamente "original", altere para "modificado"; '
        "caso contrario nao altere.",
        [
            '{"persona":"coder"}',
            _planner_deferred_response(),
            '{"changes":[{"path":"controle.txt","kind":"edit","edits":[{"operation":"replace","start_line":1,"end_line":1,"content":"modificado"}]}]}',
        ],
        approval_policy=approval,
    )

    assert result.status == "unverified", result.error
    assert workspace.joinpath("controle.txt").read_text(encoding="utf-8") == "modificado"
    assert history == ["file_reader", "code_task"]
    assert [step.get("kind") or step.get("tool") for step in progression["plan"]] == [
        "file_reader",
        "deferred_condition",
        "code_task",
    ]
    observation_id = progression["plan"][0]["_step_id"]
    assert progression["plan"][1]["observation_ref"] == observation_id
    assert progression["executed"] == ["write"]
    assert progression["waived"] == []
    assert progression["pending"] == []
    assert progression["continuations"] == 0
    assert [request.action for request in approval.requests] == [
        "code_task",
        "apply_changeset",
    ]
    assert len(gateway.payloads) == 3  # router, plan, and canonical code proposal
    assert len(gateway.calls) == 3
    assert "continuation_plan" not in str(gateway.payloads)
    assert not any(event.get("type") == "replan" for event in progression["events"])


def test_real_initial_planner_deferred_false_waives_without_effect_or_continuation(
    tmp_path: Path,
) -> None:
    approval = _CountingApproval()
    result, gateway, workspace, history, progression = _run_queued_task(
        tmp_path,
        "modificado",
        'Se controle.txt contiver exatamente "original", altere para "modificado"; '
        "caso contrario nao altere.",
        [
            '{"persona":"coder"}',
            _planner_deferred_response(),
            '{"action":"complete","reason":"a observacao confirma o estado final"}',
        ],
        approval_policy=approval,
    )

    assert result.status == "succeeded", result.error
    assert workspace.joinpath("controle.txt").read_text(encoding="utf-8") == "modificado"
    assert history == ["file_reader"]
    assert [step.get("kind") or step.get("tool") for step in progression["plan"]] == [
        "file_reader",
        "deferred_condition",
    ]
    observation_id = progression["plan"][0]["_step_id"]
    assert progression["plan"][1]["observation_ref"] == observation_id
    assert progression["executed"] == []
    assert progression["waived"] == ["write"]
    assert progression["pending"] == []
    assert progression["continuations"] == 0
    assert approval.requests == []
    assert len(gateway.payloads) == 3  # router, initial plan, and post-plan boundary
    assert len(gateway.calls) == 3
    assert "Uma fronteira semântica explícita" in gateway.payloads[2]["messages"][-1]["content"]
    assert not any(event.get("type") == "replan" for event in progression["events"])


def test_initial_multi_step_plan_is_persisted_and_executed_without_replanning(
    tmp_path: Path,
) -> None:
    result, gateway, _, history, progression = _run_queued_task(
        tmp_path,
        "alpha",
        "Compare controle.txt e outro.txt",
        [
            '{"persona":"coder"}',
            '{"action":"use_tools","plan":[{"tool":"file_reader","args":{"file_path":"controle.txt"}},{"tool":"file_reader","args":{"file_path":"outro.txt"}}]}',
            '{"action":"complete","reason":"as duas leituras foram executadas"}',
            "Os arquivos contem alpha e beta.",
        ],
        extra_files={"outro.txt": "beta"},
    )

    assert result.status == "succeeded"
    assert history == ["file_reader", "file_reader"]
    assert [step["tool"] for step in progression["plan"]] == [
        "file_reader",
        "file_reader",
    ]
    assert progression["step_statuses"] == ["completed", "completed"]
    assert progression["continuations"] == 0
    assert len(gateway.payloads) == 4  # router, initial plan, boundary, final synthesis
    assert "plano executavel completo" in gateway.payloads[1]["messages"][-1]["content"]
    assert "continuation_plan" not in str(gateway.payloads)


def test_simple_edit_executes_from_initial_plan_without_rediscovery(tmp_path: Path) -> None:
    result, gateway, workspace, history, progression = _run_queued_task(
        tmp_path,
        "original",
        "Altere controle.txt para modificado",
        [
            '{"persona":"coder"}',
            '{"action":"use_tools","plan":[{"tool":"code_task","args":{"action":"modify","objective":"Altere controle.txt para modificado","targets":["controle.txt"]}}]}',
            '{"changes":[{"path":"controle.txt","kind":"edit","edits":[{"operation":"replace","start_line":1,"end_line":1,"content":"modificado"}]}]}',
        ],
    )

    assert result.status == "unverified", result.error
    assert workspace.joinpath("controle.txt").read_text(encoding="utf-8") == "modificado"
    assert history == ["code_task"]
    assert progression["step_statuses"] == ["unverified"]
    assert progression["continuations"] == 0
    assert len(gateway.payloads) == 3  # router, plan, and canonical code proposal
    assert len(gateway.calls) == 3
    assert "continuation_plan" not in str(gateway.payloads)


def test_mutation_request_continues_after_observation(tmp_path: Path) -> None:
    result, gateway, workspace, history, progression = _run_queued_task(
        tmp_path,
        "original",
        "Altere controle.txt para que contenha apenas modificado",
        [
            '{"persona":"coder"}',
            '{"plan":[{"tool":"file_reader","args":{"file_path":"controle.txt"}}]}',
            '{"action":"execute","plan":[{"tool":"code_task","args":{"action":"modify","objective":"Altere controle.txt para que contenha apenas modificado","targets":["controle.txt"]}}]}',
            '{"changes":[{"path":"controle.txt","kind":"edit","edits":[{"operation":"replace","start_line":1,"end_line":1,"content":"modificado","expected_text":"original"}]}]}',
        ],
    )

    assert result.status == "unverified", result.error
    assert workspace.joinpath("controle.txt").read_text(encoding="utf-8") == "modificado"
    assert history == ["file_reader", "code_task"]
    assert result.answer.startswith("A tarefa terminou com status operacional: unverified.")
    assert result.receipt["mutation_occurred"] is True
    assert result.receipt["operational_outcome"]["executed_effects"] == ["write"]
    assert progression["executed"] == ["write"]
    assert progression["pending"] == []
    assert progression["terminal"] == "unverified"
    outcome_event = next(
        event for event in progression["events"] if event.get("type") == "task_outcome"
    )
    assert outcome_event["data"]["status"] == "unverified"
    assert outcome_event["data"]["mutation_occurred"] is True
    assert outcome_event["data"]["executed_effects"] == ["write"]
    assert len(gateway.payloads) == 4  # router, plan, continuation, code proposal
    continuation_grammar = gateway.payloads[2]["grammar"]
    assert "complete_without_effect" in continuation_grammar
    assert "observation_index" in continuation_grammar
    assert "effect_required" not in continuation_grammar
    assert "direct_response" not in continuation_grammar


def test_observation_only_request_does_not_continue_to_write(tmp_path: Path) -> None:
    result, _, workspace, history, progression = _run_queued_task(
        tmp_path,
        "original",
        "Leia controle.txt e diga o conteudo",
        [
            '{"persona":"coder"}',
            '{"plan":[{"tool":"file_reader","args":{"file_path":"controle.txt"}}]}',
            '{"action":"complete","reason":"a leitura basta"}',
            "O arquivo contem original.",
        ],
    )

    assert result.status == "succeeded"
    assert workspace.joinpath("controle.txt").read_text(encoding="utf-8") == "original"
    assert history == ["file_reader"]
    assert progression["requested"] == []
    assert progression["terminal"] == "complete"


def test_negated_read_request_answers_observation_without_phantom_write(
    tmp_path: Path,
) -> None:
    result, _, workspace, history, progression = _run_queued_task(
        tmp_path,
        "modificado",
        "Qual é o conteúdo atual de controle.txt? Não altere nenhum arquivo.",
        [
            '{"persona":"coder"}',
            '{"plan":[{"tool":"file_reader","args":{"file_path":"controle.txt"}}]}',
            '{"action":"complete","reason":"a observacao responde ao pedido"}',
            "O conteúdo atual de controle.txt é modificado.",
        ],
    )

    assert result.status == "succeeded"
    assert "modificado" in result.answer.casefold()
    assert workspace.joinpath("controle.txt").read_text(encoding="utf-8") == "modificado"
    assert history == ["file_reader"]
    assert progression["requested"] == []
    assert progression["pending"] == []
    assert progression["waived"] == []
    assert progression["continuations"] == 0
    assert progression["terminal"] == "complete"
    assert result.receipt["mutation_occurred"] is False


def test_blocked_partial_read_preserves_canonical_evidence_in_public_answer(
    tmp_path: Path,
) -> None:
    result, gateway, workspace, history, progression = _run_queued_task(
        tmp_path,
        "modificado",
        "Leia controle.txt e arquivo_parcial_inexistente_731904.txt. "
        "Diga o conteúdo de cada um; se algum não puder ser lido, diga claramente "
        "qual e por quê. Não altere nada.",
        [
            '{"persona":"coder"}',
            '{"plan":[{"tool":"file_reader","args":{"file_path":"controle.txt"}},'
            '{"tool":"file_reader","args":{"file_path":"arquivo_parcial_inexistente_731904.txt"}}]}',
            '{"action":"blocked","reason":"a leitura parcial falhou"}',
            "controle.txt: modificado. "
            "arquivo_parcial_inexistente_731904.txt: não foi possível ler; "
            "arquivo não encontrado.",
        ],
    )

    assert result.status == "blocked"
    assert result.success is False
    assert "status operacional: blocked" in result.answer
    assert "controle.txt" in result.answer
    assert "modificado" in result.answer
    assert "arquivo_parcial_inexistente_731904.txt" in result.answer
    assert "arquivo não encontrado" in result.answer
    assert "sucesso" not in result.answer.casefold()
    assert workspace.joinpath("controle.txt").read_text(encoding="utf-8") == "modificado"
    assert history == ["file_reader", "file_reader", "directory_lister"]
    assert progression["terminal"] == "block"
    assert progression["requested"] == []
    assert progression["pending"] == []
    assert result.receipt["operational_outcome"]["terminal_status"] == "blocked"
    assert len(gateway.payloads) == 4


def test_missing_file_task_keeps_non_success_and_explains_file_not_found(
    tmp_path: Path,
) -> None:
    result, _, _, history, progression = _run_queued_task(
        tmp_path,
        "modificado",
        "Leia o arquivo arquivo_que_nao_existe_583921.txt e me diga qual é o "
        "conteúdo dele. Não altere nada.",
        [
            '{"persona":"coder"}',
            '{"plan":[{"tool":"file_reader","args":{"file_path":"arquivo_que_nao_existe_583921.txt"}}]}',
            '{"action":"blocked","reason":"arquivo ausente"}',
            "Não foi possível fornecer o conteúdo porque o arquivo não foi encontrado.",
        ],
    )

    assert result.status == "blocked"
    assert result.success is False
    assert "arquivo_que_nao_existe_583921.txt" in result.answer
    assert "não foi encontrado" in result.answer
    assert "sucesso" not in result.answer.casefold()
    assert history == ["file_reader", "directory_lister"]
    assert progression["terminal"] == "block"


def test_incomplete_read_only_plan_must_cross_boundary_and_search_bound_value(
    tmp_path: Path,
) -> None:
    result, gateway, workspace, history, progression = _run_queued_task(
        tmp_path,
        "unrelated",
        "Leia fonte_h2.txt. Depois procure esse valor nos arquivos do workspace e me diga em quais arquivos ele aparece. Não altere nada.",
        [
            '{"persona":"coder"}',
            '{"plan":[{"tool":"file_reader","args":{"file_path":"fonte_h2.txt"}}]}',
            '{"action":"execute","plan":[{"tool":"grep","args":{"path":".","recursive":true,"max_results":20},"bindings":{"pattern":{"from_step":1,"path":[]}}}]}',
            '{"action":"complete","reason":"a busca confirmou os arquivos encontrados"}',
            "O valor orion_584271 aparece em alvo_h2_a.txt, fonte_h2.txt e alvo_h2_b.txt.",
        ],
        extra_files={
            "fonte_h2.txt": "orion_584271",
            "alvo_h2_a.txt": "orion_584271",
            "alvo_h2_b.txt": "orion_584271",
            "sem_match.txt": "outro valor",
        },
    )

    assert result.status == "succeeded"
    assert history == ["file_reader", "grep"]
    grep_entry = next(entry for entry in progression["history_entries"] if entry["tool"] == "grep")
    assert grep_entry["args"]["pattern"] == "orion_584271"
    assert "bindings" not in grep_entry["args"]
    assert "${" not in str(grep_entry["args"])
    assert progression["reasoning_turns"] == 2
    assert progression["requested"] == []
    assert progression["pending"] == []
    assert progression["waived"] == []
    assert result.receipt["mutation_occurred"] is False
    boundary_prompt = gateway.payloads[2]["messages"][-1]["content"]
    assert "orion_584271" in boundary_prompt
    assert "fonte_h2.txt" in boundary_prompt
    assert "Uma fronteira semântica explícita" in boundary_prompt
    assert workspace.joinpath("fonte_h2.txt").read_text(encoding="utf-8") == "orion_584271"


def test_post_plan_boundary_block_cannot_be_reported_as_success(tmp_path: Path) -> None:
    result, gateway, _workspace, history, progression = _run_queued_task(
        tmp_path,
        "modificado",
        "Leia controle.txt e avalie se a observacao basta.",
        [
            '{"persona":"coder"}',
            '{"plan":[{"tool":"file_reader","args":{"file_path":"controle.txt"}}]}',
            '{"action":"blocked","reason":"evidencia insuficiente"}',
            "A leitura foi observada, mas a tarefa foi bloqueada antes da conclusão.",
        ],
    )

    assert result.status == "blocked"
    assert result.success is False
    assert history == ["file_reader"]
    assert progression["terminal"] == "block"
    assert progression["reasoning_turns"] == 1
    assert len(gateway.payloads) == 4
    assert "status operacional: blocked" in result.answer
    assert "controle.txt" in result.answer


def test_invalid_post_plan_boundary_fails_closed(tmp_path: Path) -> None:
    result, _gateway, _workspace, history, progression = _run_queued_task(
        tmp_path,
        "modificado",
        "Leia controle.txt e responda com evidencia.",
        [
            '{"persona":"coder"}',
            '{"plan":[{"tool":"file_reader","args":{"file_path":"controle.txt"}}]}',
            '{}',
        ],
    )

    assert result.status == "blocked"
    assert result.success is False
    assert history == ["file_reader"]
    assert progression["terminal"] == "block"
    assert progression["reasoning_turns"] == 1


def test_already_satisfied_mutation_request_avoids_write(tmp_path: Path) -> None:
    result, gateway, workspace, history, progression = _run_queued_task(
        tmp_path,
        "modificado",
        "Altere controle.txt para que contenha apenas modificado",
        [
            '{"persona":"coder"}',
            '{"plan":[{"tool":"file_reader","args":{"file_path":"controle.txt"}}]}',
            '{"action":"complete_without_effect","observation_index":1}',
            '{"action":"complete","reason":"a dispensa foi confirmada"}',
        ],
    )

    assert result.status == "succeeded"
    assert workspace.joinpath("controle.txt").read_text(encoding="utf-8") == "modificado"
    assert history == ["file_reader"]
    assert progression["waived"] == ["write"]
    assert progression["pending"] == []
    assert progression["terminal"] == "complete"
    assert result.answer.startswith("Nenhuma escrita foi executada.")
    assert "alterad" not in result.answer.casefold()
    assert result.receipt["mutation_occurred"] is False
    assert result.receipt["operational_outcome"]["waived_effects"] == ["write"]
    outcome_event = next(
        event for event in progression["events"] if event.get("type") == "task_outcome"
    )
    assert outcome_event["data"]["mutation_occurred"] is False
    assert outcome_event["data"]["waived_effects"] == ["write"]
    assert sum(event.get("type") == "task_outcome" for event in progression["events"]) == 1
    waiver = next(
        event
        for event in progression["events"]
        if event.get("type") == "effect_waiver_bound"
    )
    assert waiver["data"]["observation_index"] == 1
    assert len(gateway.payloads) == 4
    continuation_prompt = gateway.payloads[2]["messages"][-1]["content"]
    assert "obrigacao ainda nao resolvida" in continuation_prompt
    assert "nao uma ordem para executar" in continuation_prompt
    assert "Primeiro confronte o objetivo condicional" in continuation_prompt
    assert '1: status=completed, tool="file_reader"' in continuation_prompt
    assert "Nao repita uma observacao ja concluida com sucesso" in continuation_prompt


def test_noop_code_task_does_not_prove_a_pending_write(tmp_path: Path) -> None:
    result, _, workspace, history, progression = _run_queued_task(
        tmp_path,
        "modificado",
        "Se controle.txt for original, altere para modificado; caso contrario nao altere.",
        [
            '{"persona":"coder"}',
            '{"plan":[{"tool":"file_reader","args":{"file_path":"controle.txt"}}]}',
            '{"action":"execute","plan":[{"tool":"code_task","args":{"action":"modify","objective":"Mantenha controle.txt como modificado","targets":["controle.txt"]}}]}',
            '{"changes":[{"path":"controle.txt","kind":"modify","content":"modificado"}]}',
        ],
    )

    assert result.status == "unverified"
    assert result.success is False
    assert workspace.joinpath("controle.txt").read_text(encoding="utf-8") == "modificado"
    assert history == ["file_reader", "code_task"]
    assert progression["executed"] == []
    assert progression["waived"] == []
    assert progression["pending"] == ["write"]
    assert progression["terminal"] == "unverified"
    assert result.receipt["mutation_occurred"] is False
    assert result.receipt["operational_outcome"]["pending_effects"] == ["write"]
    outcome_event = next(
        event for event in progression["events"] if event.get("type") == "task_outcome"
    )
    assert outcome_event["data"]["status"] == "unverified"
    assert outcome_event["data"]["pending_effects"] == ["write"]


def test_effect_waiver_rejects_unbound_observation_reference(tmp_path: Path) -> None:
    result, gateway, workspace, history, progression = _run_queued_task(
        tmp_path,
        "modificado",
        "Altere controle.txt para que contenha apenas modificado",
        [
            '{"persona":"coder"}',
            '{"plan":[{"tool":"file_reader","args":{"file_path":"controle.txt"}}]}',
            '{"action":"complete_without_effect","observation_index":2}',
        ],
    )

    assert result.status == "blocked"
    assert result.error == "requested_effect_pending"
    assert workspace.joinpath("controle.txt").read_text(encoding="utf-8") == "modificado"
    assert history == ["file_reader"]
    assert progression["waived"] == []
    assert progression["pending"] == ["write"]
    assert progression["terminal"] == "block"
    assert len(gateway.payloads) == 3


def test_continuation_claim_cannot_replace_missing_write_evidence(tmp_path: Path) -> None:
    result, gateway, workspace, history, progression = _run_queued_task(
        tmp_path,
        "original",
        "Primeiro leia controle.txt. Se o conteudo for exatamente original, altere para modificado.",
        [
            '{"persona":"coder"}',
            '{"plan":[{"tool":"file_reader","args":{"file_path":"controle.txt"}}]}',
            '{"action":"complete_without_effect","observation_index":1,"answer":"O arquivo e original; portanto ainda preciso usar code_task."}',
        ],
    )

    assert result.status == "blocked"
    assert result.error == "requested_effect_pending"
    assert workspace.joinpath("controle.txt").read_text(encoding="utf-8") == "original"
    assert history == ["file_reader"]
    assert progression["waived"] == []
    assert progression["pending"] == ["write"]
    assert progression["terminal"] == "block"
    assert len(gateway.payloads) == 3
    continuation_prompt = gateway.payloads[2]["messages"][-1]["content"]
    assert "nenhum efeito de escrita executado" in continuation_prompt
    assert "nao prova execucao nem dispensa efeito" in continuation_prompt
    assert "1: tool=\"file_reader\"" in continuation_prompt


def test_read_only_mutation_request_remains_blocked_before_approval(tmp_path: Path) -> None:
    result, gateway, workspace, history, progression = _run_queued_task(
        tmp_path,
        "original",
        "Altere controle.txt para que contenha apenas modificado",
        [
            '{"persona":"coder"}',
            '{"plan":[{"tool":"file_reader","args":{"file_path":"controle.txt"}}]}',
            '{"action":"execute","plan":[{"tool":"code_task","args":{"target":"controle.txt","content":"modificado"}},{"tool":"file_reader","args":{"file_path":"controle.txt"}}]}',
        ],
        mode=OperationalMode.READ_ONLY,
    )

    assert result.status == "blocked"
    assert result.error == "requested_effect_pending"
    assert workspace.joinpath("controle.txt").read_text(encoding="utf-8") == "original"
    assert history == ["file_reader"]
    proposed = next(
        event
        for event in progression["events"]
        if event.get("type") == "continuation_plan_proposed"
    )
    assert [step["tool"] for step in proposed["data"]["plan"]] == [
        "code_task",
        "file_reader",
    ]
    assert any(event["type"] == "hard_block" for event in progression["events"])
    assert progression["pending"] == ["write"]
    assert progression["terminal"] == "block"
    assert len(gateway.payloads) >= 3


def test_malformed_code_task_arguments_reach_canonical_validation(tmp_path: Path) -> None:
    result, _, workspace, history, progression = _run_queued_task(
        tmp_path,
        "original",
        "Altere controle.txt para modificado",
        [
            '{"persona":"coder"}',
            '{"plan":[{"tool":"code_task","args":{"target":"controle.txt","content":"modificado"}}]}',
        ],
    )

    assert result.status == "blocked"
    assert workspace.joinpath("controle.txt").read_text(encoding="utf-8") == "original"
    assert history == []
    assert progression["terminal"] == "block"
    hard_block = next(
        event for event in progression["events"] if event.get("type") == "hard_block"
    )
    assert "unknown argument(s): content, target" in str(
        hard_block["data"].get("errors")
    )


def test_initial_final_text_cannot_claim_unexecuted_write(tmp_path: Path) -> None:
    result, _, workspace, history, progression = _run_queued_task(
        tmp_path,
        "original",
        "Altere controle.txt para modificado",
        [
            '{"persona":"coder"}',
            '{"action":"direct_response","answer":"Arquivo alterado com sucesso."}',
        ],
    )

    assert result.status == "blocked"
    assert result.error == "requested_effect_pending"
    assert "permanece pendente" in result.answer
    assert workspace.joinpath("controle.txt").read_text(encoding="utf-8") == "original"
    assert history == []
    assert progression["pending"] == ["write"]
    assert progression["terminal"] == "block"


def test_reactive_final_cannot_claim_pending_write(tmp_path: Path) -> None:
    result, _, workspace, history, progression = _run_queued_task(
        tmp_path,
        "original",
        "Altere controle.txt para modificado",
        [
            '{"persona":"coder"}',
            '{"action":"replan"}',
            '{"action":"final","answer":"Arquivo alterado com sucesso."}',
        ],
    )

    assert result.status == "blocked"
    assert result.error == "requested_effect_pending"
    assert "permanece pendente" in result.answer
    assert workspace.joinpath("controle.txt").read_text(encoding="utf-8") == "original"
    assert history == []
    assert progression["pending"] == ["write"]
    assert progression["terminal"] == "block"


def test_hierarchical_final_cannot_claim_pending_write(tmp_path: Path) -> None:
    objective = (
        "Analise controle.txt e depois modifique controle.txt para modificado "
        "e depois modifique controle.txt para modificado"
    )
    macro = {
        "steps": [
            {
                "id": "s1",
                "title": "Editar",
                "goal": "Altere controle.txt para modificado",
                "priority": "medium",
                "depends_on": [],
                "estimated_tools": [],
            }
        ]
    }
    result, gateway, workspace, history, progression = _run_queued_task(
        tmp_path,
        "original",
        objective,
        [
            '{"persona":"coder"}',
            json.dumps(macro),
            '{"action":"direct_response","answer":"Arquivo alterado com sucesso."}',
        ],
    )

    assert result.status == "blocked"
    assert result.error == "requested_effect_pending"
    assert "permanece pendente" in result.answer
    assert workspace.joinpath("controle.txt").read_text(encoding="utf-8") == "original"
    assert history == []
    assert progression["pending"] == ["write"]
    assert progression["terminal"] == "block"
    assert len(gateway.payloads) == 4  # router, macro plan, micro plan, summary flush


def test_security_final_cannot_claim_pending_write(tmp_path: Path) -> None:
    result, _, workspace, history, progression = _run_queued_task(
        tmp_path,
        "original",
        "Analise app.py; corrija app.py para vulnerabilidade",
        ['{"persona":"security_auditor"}'],
        extra_files={"app.py": "value = eval(input())\n"},
    )

    assert result.status == "blocked"
    assert result.error == "requested_effect_pending"
    assert "permanece pendente" in result.answer
    assert workspace.joinpath("app.py").read_text(encoding="utf-8") == "value = eval(input())\n"
    assert history == ["code_analyzer"]
    assert progression["pending"] == ["write"]
    assert progression["terminal"] == "block"


def test_hierarchical_failure_is_not_overwritten_by_later_success(tmp_path: Path) -> None:
    objective = "Analise todos os arquivos e depois leia controle.txt"
    macro = {
        "steps": [
            {
                "id": "s1",
                "title": "Falha",
                "goal": "Leia missing.txt",
                "priority": "medium",
                "depends_on": [],
            },
            {
                "id": "s2",
                "title": "Sucesso",
                "goal": "Leia controle.txt",
                "priority": "medium",
                "depends_on": [],
            },
        ]
    }
    result, _, workspace, _, progression = _run_queued_task(
        tmp_path,
        "original",
        objective,
        [
            '{"persona":"coder"}',
            json.dumps(macro),
            '{"action":"use_tools","plan":[{"tool":"file_reader","args":{"file_path":"missing.txt"}}]}',
            '{"action":"blocked","reason":"o subobjetivo falhou antes da conclusao"}',
            '{"action":"use_tools","plan":[{"tool":"file_reader","args":{"file_path":"controle.txt"}}]}',
            '{"action":"complete","reason":"a segunda leitura foi concluida"}',
        ],
        )

    assert result.status == "blocked"
    assert result.success is False
    assert "status operacional: blocked" in result.answer
    assert "controle.txt" in result.answer
    assert "original" in result.answer
    assert "sucesso" not in result.answer.casefold()
    assert workspace.joinpath("controle.txt").read_text(encoding="utf-8") == "original"
    assert progression["terminal"] == "block"
    assert any(event.get("type") == "step_failed" for event in progression["events"])


def test_application_runs_trivial_task_with_explicit_workspace(tmp_path: Path) -> None:
    paths = _initialized_paths(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with AgentApplication.create(
        paths=paths,
        workspace=workspace,
        gateway=OfflineLegacyGateway("unused"),
        configure_logging=False,
    ) as application:
        result = application.run("oi")

        assert result.success is True
        assert "olá" in result.answer.casefold()
        assert application.workspace.root == workspace.resolve()
        assert application.workspace_paths.state_dir.is_relative_to(paths.state_dir)
        skill_roots = [
            Path(skill.base_dir).resolve()
            for skill in application.orchestrator.skills.values()
            if hasattr(skill, "base_dir")
        ]
        assert skill_roots
        assert set(skill_roots) == {workspace.resolve()}

    assert not (workspace / ".temp_analysis").exists()
    assert application.workspace_paths.memory_db_file.exists()


def test_application_loads_ready_extension_from_catalog_and_workspace(tmp_path: Path) -> None:
    paths = _initialized_paths(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manifest = tmp_path / "extension.json"
    manifest.write_text(
        json.dumps({
            "id": "demo.extension",
            "version": "1.0.0",
            "protocol_version": "1.0",
                "transport": "stdio",
                "entrypoint": ["python", "demo.py"],
                "timeout_seconds": 5,
                "tools": [{"name": "demo_tool", "schema": {}, "capabilities": ["read", "process"]}],
        }),
        encoding="utf-8",
    )
    catalog = ExtensionCatalogService(ExtensionCatalogStorage(paths.extensions_catalog_file))
    catalog.add(manifest)
    workspace_id = WorkspaceContext.create(workspace).workspace_id
    workspace_extensions = WorkspaceExtensionService.for_workspace(paths, workspace_id, catalog)
    workspace_extensions.enable("demo.extension")
    workspace_extensions.grant("demo.extension", "read")
    workspace_extensions.grant("demo.extension", "process")
    with AgentApplication.create(
        paths=paths,
        workspace=workspace,
        gateway=OfflineLegacyGateway("unused"),
        configure_logging=False,
    ) as application:
        assert "demo_tool" in application.tool_registry.names()


def test_extension_aware_bootstrap_does_not_start_processes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _initialized_paths(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manifest = tmp_path / "extension" / "manifest.json"
    manifest.parent.mkdir()
    manifest.write_text(
        json.dumps(
            {
                "id": "demo.extension",
                "version": "1.0.0",
                "protocol_version": "1.0",
                "transport": "stdio",
                "entrypoint": ["${python}", "${extension_dir}/demo.py"],
                "timeout_seconds": 5,
                "tools": [{"name": "demo_tool", "schema": {}, "capabilities": ["read", "process"]}],
            }
        ),
        encoding="utf-8",
    )
    catalog = ExtensionCatalogService(ExtensionCatalogStorage(paths.extensions_catalog_file))
    catalog.add(manifest)
    workspace_id = WorkspaceContext.create(workspace).workspace_id
    workspace_extensions = WorkspaceExtensionService.for_workspace(paths, workspace_id, catalog)
    workspace_extensions.enable("demo.extension")
    workspace_extensions.grant("demo.extension", "read")
    workspace_extensions.grant("demo.extension", "process")

    def forbidden(*args, **kwargs):
        raise AssertionError("bootstrap iniciou processo externo")

    monkeypatch.setattr("subprocess.Popen", forbidden)
    monkeypatch.setattr("subprocess.run", forbidden)
    monkeypatch.setattr("os.system", forbidden)
    monkeypatch.setattr("asyncio.create_subprocess_exec", forbidden)
    monkeypatch.setattr("asyncio.create_subprocess_shell", forbidden)
    with AgentApplication.create(
        paths=paths,
        workspace=workspace,
        gateway=OfflineLegacyGateway("unused"),
        configure_logging=False,
    ) as application:
        assert "echo" in application.tool_registry.names()
        assert "demo_tool" in application.tool_registry.names()
        adapter = application.tool_registry._descriptors_cache["demo_tool"][0]
        assert adapter.cwd == workspace.resolve()
        assert application.tool_registry.frozen is True


def test_extension_bootstrap_acceptance_drift_replace_and_workspace_isolation(tmp_path: Path) -> None:
    paths = _initialized_paths(tmp_path)
    workspace_a = tmp_path / "workspace-a"
    workspace_b = tmp_path / "workspace-b"
    workspace_a.mkdir()
    workspace_b.mkdir()
    manifest = tmp_path / "extension" / "manifest.json"
    manifest.parent.mkdir()

    def write_manifest(path: Path, tool_name: str, version: str) -> None:
        path.write_text(
            json.dumps(
                {
                    "id": "demo.extension",
                    "version": version,
                    "protocol_version": "1.0",
                    "transport": "stdio",
                    "entrypoint": ["${python}", "${extension_dir}/demo.py"],
                    "timeout_seconds": 5,
                    "tools": [{"name": tool_name, "schema": {}, "capabilities": ["read", "process"]}],
                }
            ),
            encoding="utf-8",
        )

    write_manifest(manifest, "demo_tool", "1.0.0")
    catalog = ExtensionCatalogService(ExtensionCatalogStorage(paths.extensions_catalog_file))
    added = catalog.add(manifest)
    workspace_id_a = WorkspaceContext.create(workspace_a).workspace_id
    workspace_id_b = WorkspaceContext.create(workspace_b).workspace_id
    service_a = WorkspaceExtensionService.for_workspace(paths, workspace_id_a, catalog)
    service_a.enable("demo.extension")
    service_a.grant("demo.extension", "read")
    service_a.grant("demo.extension", "process")

    with AgentApplication.create(
        paths=paths,
        workspace=workspace_a,
        gateway=OfflineLegacyGateway("unused"),
        configure_logging=False,
    ) as application_a:
        old_names = application_a.tool_registry.names()
        assert "demo_tool" in old_names
        assert "echo" in old_names

    application_b = ApplicationExtensionBootstrap(
        paths, workspace_id_b, workspace_b
    ).build(BuiltinToolAdapter(load_skill_registry(base_dir=workspace_b)))
    assert "demo_tool" not in application_b.registry.names()
    assert "echo" in application_b.registry.names()

    write_manifest(manifest, "changed_tool", "1.0.1")
    drift = ApplicationExtensionBootstrap(
        paths, workspace_id_a, workspace_a
    ).build(BuiltinToolAdapter(load_skill_registry(base_dir=workspace_a)))
    assert "changed_tool" not in drift.registry.names()
    assert drift.materialization.bindings == ()
    assert application_a.tool_registry.names() == old_names

    replacement = tmp_path / "extension" / "replacement.json"
    write_manifest(replacement, "replacement_tool", "2.0.0")
    catalog.replace(
        "demo.extension",
        replacement,
        expected_fingerprint=added.entry.manifest_sha256,
    )
    replaced = ApplicationExtensionBootstrap(
        paths, workspace_id_a, workspace_a
    ).build(BuiltinToolAdapter(load_skill_registry(base_dir=workspace_a)))
    assert "replacement_tool" in replaced.registry.names()
    assert "demo_tool" not in replaced.registry.names()


def test_resume_restores_persona_and_capabilities(tmp_path: Path) -> None:
    paths = _initialized_paths(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with AgentApplication.create(
        paths=paths,
        workspace=workspace,
        gateway=OfflineLegacyGateway("unused"),
        configure_logging=False,
    ) as application:
        assert "web_search" in application.orchestrator.tool_registry.names()
        state = application.orchestrator.agent_state
        state.objective = "pesquise notícias sobre IA"
        state.persona = "researcher"
        state.persona_prompt = "Você é um pesquisador."
        state.root_task_id = application.orchestrator.run_correlation.root_task_id
        state.task_definition_ref = (
            application.orchestrator.task_definition_compiler.compile(
                state.root_task_id,
                state.objective,
            )
        )
        state.set_plan([
            {"tool": "web_search", "args": {"query": "IA"}},
        ])
        application.orchestrator._save_checkpoint()

    with AgentApplication.create(
        paths=paths,
        workspace=workspace,
        gateway=OfflineLegacyGateway("unused"),
        configure_logging=False,
    ) as resumed_application:
        called: dict[str, object] = {}

        def fake_run_tool(tool_name: str, args, record_result: bool = True):
            called["tool_name"] = tool_name
            called["args"] = args
            called["active_skills"] = list(resumed_application.orchestrator.active_skills)
            called["allowed_capabilities"] = resumed_application.orchestrator.allowed_capabilities
            return {
                "ok": True,
                "done": True,
                "status": "succeeded",
                "data": {"result": "ok"},
            }

        resumed_application.orchestrator.tool_executor.run_tool = fake_run_tool
        result = resumed_application.run(None)

        assert "web_search" in resumed_application.orchestrator.active_skills
        assert "network" in resumed_application.orchestrator.allowed_capabilities
        assert called["tool_name"] == "web_search"
        assert called["args"] == {"query": "IA"}
        assert result is not None


def test_workspaces_do_not_share_state_or_scratch(tmp_path: Path) -> None:
    paths = _initialized_paths(tmp_path)
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()

    with AgentApplication.create(
        paths=paths,
        workspace=first,
        gateway=OfflineLegacyGateway("unused"),
        configure_logging=False,
    ) as first_application:
        first_paths = first_application.workspace_paths
    with AgentApplication.create(
        paths=paths,
        workspace=second,
        gateway=OfflineLegacyGateway("unused"),
        configure_logging=False,
    ) as second_application:
        second_paths = second_application.workspace_paths

    assert first_paths.state_dir != second_paths.state_dir
    assert first_paths.memory_db_file != second_paths.memory_db_file
    assert first_paths.scratch_dir != second_paths.scratch_dir


def test_workspace_config_is_not_loaded_implicitly(tmp_path: Path) -> None:
    paths = _initialized_paths(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "config.json").write_text('{"auto_confirm": true}', encoding="utf-8")

    with AgentApplication.create(
        paths=paths,
        workspace=workspace,
        gateway=OfflineLegacyGateway("unused"),
        configure_logging=False,
    ) as application:
        assert application.config["auto_confirm"] is False


def test_application_requires_initialized_configuration(tmp_path: Path) -> None:
    paths = AppPaths.discover(tmp_path / "home", env={})
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with pytest.raises(ConfigNotFound):
        AgentApplication.create(
            paths=paths,
            workspace=workspace,
            gateway=OfflineLegacyGateway("unused"),
            configure_logging=False,
        )

    assert not paths.data_dir.exists()
    assert not paths.state_dir.exists()
    assert not paths.log_dir.exists()


def test_application_rejects_corrupt_memory_json_and_releases_bootstrap_lock(
    tmp_path: Path,
) -> None:
    paths = _initialized_paths(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    context = WorkspaceContext.create(workspace)
    workspace_paths = paths.for_workspace(context.workspace_id)
    workspace_paths.ensure_directories()
    workspace_paths.memory_file.write_text('{"notes": ', encoding="utf-8")

    with pytest.raises(MemoryLoadError):
        AgentApplication.create(
            paths=paths,
            workspace=workspace,
            gateway=OfflineLegacyGateway("unused"),
            configure_logging=False,
        )

    assert not workspace_paths.lock_file.exists()
    workspace_paths.memory_file.write_text("{}", encoding="utf-8")
    with AgentApplication.create(
        paths=paths,
        workspace=workspace,
        gateway=OfflineLegacyGateway("unused"),
        configure_logging=False,
    ):
        pass


def test_startup_failure_releases_only_owned_resources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _initialized_paths(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    context = WorkspaceContext.create(workspace)
    workspace_paths = paths.for_workspace(context.workspace_id)

    def fail_bootstrap(*_args, **_kwargs):
        raise RuntimeError("falha de bootstrap")

    monkeypatch.setattr(
        "agent.application.ApplicationExtensionBootstrap.build",
        fail_bootstrap,
    )
    with pytest.raises(RuntimeError, match="falha de bootstrap"):
        AgentApplication.create(
            paths=paths,
            workspace=workspace,
            gateway=OfflineLegacyGateway("unused"),
            configure_logging=False,
        )

    assert not workspace_paths.lock_file.exists()


def test_close_releases_lock_when_memory_persistence_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _initialized_paths(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    application = AgentApplication.create(
        paths=paths,
        workspace=workspace,
        gateway=OfflineLegacyGateway("unused"),
        configure_logging=False,
    )
    monkeypatch.setattr(
        application.orchestrator,
        "_persist_memory_to_file",
        lambda: (_ for _ in ()).throw(RuntimeError("persistência indisponível")),
    )

    with pytest.raises(RuntimeError, match="persistência indisponível"):
        application.close()

    assert application._closed is True
    assert not application.workspace_paths.lock_file.exists()

    with AgentApplication.create(
        paths=paths,
        workspace=workspace,
        gateway=OfflineLegacyGateway("unused"),
        configure_logging=False,
    ):
        pass


def test_workspace_identity_is_stable_across_path_alias_and_projections(
    tmp_path: Path,
) -> None:
    paths = _initialized_paths(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    alias = tmp_path / "workspace-alias"
    try:
        alias.symlink_to(workspace, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink indisponível no ambiente: {exc}")

    direct = WorkspaceContext.create(workspace)
    aliased = WorkspaceContext.create(alias)
    assert direct.root == aliased.root
    assert direct.workspace_id == aliased.workspace_id
    assert paths.for_workspace(direct.workspace_id) == paths.for_workspace(
        aliased.workspace_id
    )


def test_terminal_checkpoint_cannot_resume_as_fresh_success(tmp_path: Path) -> None:
    paths = _initialized_paths(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with AgentApplication.create(
        paths=paths,
        workspace=workspace,
        gateway=OfflineLegacyGateway("unused"),
        configure_logging=False,
    ) as application:
        state = application.orchestrator.agent_state
        state.objective = "tarefa terminal"
        state.terminal_disposition = "complete"
        application.orchestrator._save_checkpoint()

    with AgentApplication.create(
        paths=paths,
        workspace=workspace,
        gateway=OfflineLegacyGateway("unused"),
        configure_logging=False,
    ) as resumed:
        result = resumed.run(None)

    assert result.status == "blocked"
    assert result.success is False
    assert result.receipt["operational_outcome"]["terminal_status"] == "blocked"


def test_incompatible_checkpoint_fails_safely_without_starting_new_task(
    tmp_path: Path,
) -> None:
    paths = _initialized_paths(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    context = WorkspaceContext.create(workspace)
    workspace_paths = paths.for_workspace(context.workspace_id)
    workspace_paths.ensure_directories()
    checkpoint = workspace_paths.checkpoint_file
    checkpoint.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "objective": "tarefa antiga",
                "plan": [],
                "step_records": [],
            }
        ),
        encoding="utf-8",
    )

    with AgentApplication.create(
        paths=paths,
        workspace=workspace,
        gateway=OfflineLegacyGateway("unused"),
        configure_logging=False,
    ) as application:
        result = application.run(None)

    assert result.status == "blocked"
    assert result.success is False
    assert "CHECKPOINT_INCOMPATIBLE_SCHEMA" in result.error
    assert checkpoint.exists()


def test_close_is_idempotent(tmp_path: Path) -> None:
    paths = _initialized_paths(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    application = AgentApplication.create(
        paths=paths,
        workspace=workspace,
        gateway=OfflineLegacyGateway("unused"),
        configure_logging=False,
    )

    application.close()
    application.close()

    with pytest.raises(RuntimeError, match="encerrada"):
        application.run("oi")


def test_close_does_not_persist_memory_twice_after_a_task(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _initialized_paths(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    application = AgentApplication.create(
        paths=paths,
        workspace=workspace,
        gateway=OfflineLegacyGateway("unused"),
        configure_logging=False,
    )
    memory = application.orchestrator.agent_state.memory
    real_persist = memory.persist_to_file
    calls = 0

    def counted_persist(path=None):
        nonlocal calls
        calls += 1
        return real_persist(path)

    monkeypatch.setattr(memory, "persist_to_file", counted_persist)

    application.run("oi")
    application.close()

    assert calls == 1


def test_same_workspace_state_rejects_second_live_application(tmp_path: Path) -> None:
    paths = _initialized_paths(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    first = AgentApplication.create(
        paths=paths,
        workspace=workspace,
        gateway=OfflineLegacyGateway("unused"),
        configure_logging=False,
    )
    try:
        with pytest.raises(InstanceLockError, match="em uso"):
            AgentApplication.create(
                paths=paths,
                workspace=workspace,
                gateway=OfflineLegacyGateway("unused"),
                configure_logging=False,
            )
    finally:
        first.close()

    with AgentApplication.create(
        paths=paths,
        workspace=workspace,
        gateway=OfflineLegacyGateway("unused"),
        configure_logging=False,
    ):
        pass


def test_process_stdout_capture_serializes_concurrent_applications(tmp_path: Path) -> None:
    paths = _initialized_paths(tmp_path)
    roots = (tmp_path / "first", tmp_path / "second")
    for root in roots:
        root.mkdir()
    applications = [
        AgentApplication.create(
            paths=paths,
            workspace=root,
            gateway=OfflineLegacyGateway("unused"),
            configure_logging=False,
        )
        for root in roots
    ]
    guard = threading.Lock()
    active = 0
    maximum_active = 0

    def instrumented_run(application: AgentApplication):
        def run(_: str) -> str:
            nonlocal active, maximum_active
            with guard:
                active += 1
                maximum_active = max(maximum_active, active)
            time.sleep(0.03)
            with guard:
                active -= 1
            application.orchestrator.agent_state.terminal_disposition = "complete"
            return "ok"

        return run

    try:
        for application in applications:
            application.orchestrator.run = instrumented_run(application)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(
                pool.map(
                    lambda application: application.run("objetivo"),
                    applications,
                )
            )
    finally:
        for application in applications:
            application.close()

    assert all(result.success for result in results)
    assert maximum_active == 1


def test_model_planned_file_writer_is_excluded_with_auto_approval_and_no_mutation(
    tmp_path: Path,
) -> None:
    paths = _initialized_paths(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    gateway = OfflineLegacyGateway("unused")
    gateway.responses = [
        '{"persona":"coder"}',
        '{"tools":["code_task"]}',
        '{"plan":[{"tool":"file_writer","args":{"action":"write","file_path":"headless.txt","content":"não aplicar\\n"}}]}',
    ]
    application = AgentApplication.create(
        paths=paths,
        workspace=workspace,
        gateway=gateway,
        approval_policy=AutoApprove(),
        configure_logging=False,
    )
    try:
        result = application.run("escreva headless.txt")
        planning_view = application.orchestrator.get_planning_view("linear")

        assert application.orchestrator.current_persona == "coder"
        assert "code_task" in application.orchestrator.active_skills
        assert "file_writer" not in application.orchestrator.active_skills
        assert planning_view is not None
        assert "code_task" in planning_view.presented_names
        assert "file_writer" not in planning_view.presented_names
        assert application.orchestrator.agent_state.tool_history == []
        assert result.status == "blocked"
        assert result.success is False
        assert not (workspace / "headless.txt").exists()
    finally:
        application.close()


def test_unverified_tool_status_reaches_application_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _initialized_paths(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    application = AgentApplication.create(
        paths=paths,
        workspace=workspace,
        gateway=OfflineLegacyGateway("unused"),
        configure_logging=False,
    )
    try:
        def run_unverified(_objective):
            application.orchestrator.agent_state.record_tool_result(
                "code_task",
                {},
                {
                    "ok": False,
                    "done": False,
                    "status": "unverified",
                    "message": "Validação indisponível.",
                },
            )
            return "Validação indisponível."

        monkeypatch.setattr(application.orchestrator, "run", run_unverified)

        result = application.run("modifique e valide")

        assert result.status == "unverified"
        assert result.success is False
    finally:
        application.close()


def test_internal_notes_use_workspace_scratch_not_launch_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _initialized_paths(tmp_path)
    workspace = tmp_path / "workspace"
    launch_cwd = tmp_path / "launch-cwd"
    workspace.mkdir()
    launch_cwd.mkdir()
    sentinel = launch_cwd / "analysis_notes.md"
    sentinel.write_text("sentinela externa", encoding="utf-8")
    monkeypatch.chdir(launch_cwd)

    with AgentApplication.create(
        paths=paths,
        workspace=workspace,
        gateway=OfflineLegacyGateway("unused"),
        configure_logging=False,
    ) as application:
        notes_file = application.workspace_paths.scratch_dir / "analysis_notes.md"
        assert application.orchestrator.analysis_notes_file == notes_file

        notes_file.write_text("nota interna antiga", encoding="utf-8")
        application.orchestrator.plan_builder._clear_analysis_notes()

        assert notes_file.read_text(encoding="utf-8") == ""
        assert sentinel.read_text(encoding="utf-8") == "sentinela externa"

        notes_file.write_text("nota interna nova", encoding="utf-8")
        assert application.orchestrator.final_responder._read_notes() == "nota interna nova"
        assert sentinel.read_text(encoding="utf-8") == "sentinela externa"


def test_auto_coder_reads_tests_and_writes_only_inside_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _initialized_paths(tmp_path)
    workspace = tmp_path / "workspace"
    launch_cwd = tmp_path / "launch-cwd"
    workspace.mkdir()
    launch_cwd.mkdir()
    workspace_source = workspace / "sample.py"
    cwd_sentinel = launch_cwd / "sample.py"
    workspace_source.write_text(
        "def target():\n    return 'workspace-original'\n",
        encoding="utf-8",
    )
    sentinel_content = "def sentinel():\n    return 'cwd-sentinel'\n"
    cwd_sentinel.write_text(sentinel_content, encoding="utf-8")
    monkeypatch.chdir(launch_cwd)

    with AgentApplication.create(
        paths=paths,
        workspace=workspace,
        gateway=OfflineLegacyGateway("unused"),
        configure_logging=False,
    ) as application:
        auto_coder = application.orchestrator.auto_coder
        tested_paths: list[Path] = []

        monkeypatch.setattr(
            auto_coder,
            "generate_tests",
            lambda code, file_path: "assert target() == 'workspace-fixed'",
        )
        monkeypatch.setattr(
            auto_coder,
            "correct_code",
            lambda original, file_path, tests, error: (
                "def target():\n    return 'workspace-fixed'\n"
            ),
        )

        def run_generated_tests(
            file_path: Path,
            code: str,
            test_code: str,
        ) -> tuple[bool, str]:
            del code, test_code
            tested_paths.append(file_path)
            return len(tested_paths) > 1, "falha simulada"

        monkeypatch.setattr(
            auto_coder,
            "_run_generated_tests",
            run_generated_tests,
        )

        assert auto_coder.test_and_correct("sample.py", "corrigir sample.py") is True
        assert tested_paths == [workspace_source.resolve(), workspace_source.resolve()]
        assert (
            workspace_source.read_text(encoding="utf-8")
            == "def target():\n    return 'workspace-fixed'\n"
        )
        assert cwd_sentinel.read_text(encoding="utf-8") == sentinel_content

        with pytest.raises(ValueError, match="fora do workspace"):
            auto_coder.test_and_correct(str(cwd_sentinel), "arquivo externo")


def test_cache_and_summary_hash_the_workspace_file_not_cwd_sentinel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _initialized_paths(tmp_path)
    workspace = tmp_path / "workspace"
    launch_cwd = tmp_path / "launch-cwd"
    workspace.mkdir()
    launch_cwd.mkdir()
    workspace_source = workspace / "sample.py"
    cwd_sentinel = launch_cwd / "sample.py"
    workspace_content = "workspace = True\n" * 30
    sentinel_content = "cwd = True\n" * 30
    workspace_source.write_text(workspace_content, encoding="utf-8")
    cwd_sentinel.write_text(sentinel_content, encoding="utf-8")
    workspace_hash = hashlib.sha256(workspace_content.encode("utf-8")).hexdigest()
    monkeypatch.chdir(launch_cwd)

    with AgentApplication.create(
        paths=paths,
        workspace=workspace,
        gateway=OfflineLegacyGateway("unused"),
        configure_logging=False,
    ) as application:
        orchestrator = application.orchestrator
        memory = orchestrator.agent_state.memory.state
        memory.setdefault("file_hashes", {})["sample.py"] = workspace_hash
        memory.setdefault("file_summaries", {})["sample.py"] = "resumo em cache"

        cache_hit, cached = StepPolicies(orchestrator).try_cache(
            "file_reader",
            {"file_path": "sample.py"},
            "sample.py",
        )

        assert cache_hit is True
        assert cached is not None
        assert cached["data"] == "resumo em cache"

        memory["file_hashes"].clear()
        tool_executor = orchestrator.tool_executor
        monkeypatch.setattr(
            tool_executor,
            "summarize_text",
            lambda text, context="": "resumo atualizado",
        )
        tool_executor.maybe_summarize_and_store(
            "file_reader",
            {"file_path": "sample.py"},
            {
                "ok": True,
                "done": True,
                "data": workspace_content,
            },
        )

        assert memory["file_hashes"]["sample.py"] == workspace_hash
        assert cwd_sentinel.read_text(encoding="utf-8") == sentinel_content

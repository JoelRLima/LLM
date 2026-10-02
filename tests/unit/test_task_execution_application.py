"""C10 composition, authority and finite primitive-boundary sentinels."""

from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest

from llm_agent.agent.approval import ApprovalDecision, AutoApprove, RequireExplicitApproval
from llm_agent.agent.runtime.correlation import RunCorrelation
from llm_agent.agent.runtime.event_kinds import RuntimeEventKind
from llm_agent.agent.runtime.events import RuntimeEvent
from llm_agent.agent.tools.authority import OperationalMode
from llm_agent.application import task_execution as api
from llm_agent.application.context import AppPaths
from llm_agent.application.conversation import _resolve_gateway, bind_conversation


@pytest.mark.parametrize("interactive,automatic,policy,mode", [
    (True, False, None, OperationalMode.READ_ONLY),
    (False, True, AutoApprove, None),
    (False, False, RequireExplicitApproval, None),
])
def test_startup_one_owner_and_exact_inputs(monkeypatch, tmp_path, interactive, automatic, policy, mode):
    calls = []
    gateway = object()
    session = SimpleNamespace(gateway=gateway)
    orchestrator = SimpleNamespace(session=session)
    closed = []
    owner = SimpleNamespace(session=session, orchestrator=orchestrator, close=lambda: closed.append(owner))
    monkeypatch.setattr(api._AgentApplication, "create", lambda **kwargs: calls.append(kwargs) or owner)
    start = api.TaskExecutionStart(tmp_path, AppPaths.discover(tmp_path / "home", env={}),
                                  "configuration", "profile", ("read", "process"), "normal", False)
    approval = object()
    runtime = api.start_interactive_session(start, approval) if interactive else api.start_headless_task(
        start, automatic_approval=automatic,
    )
    assert len(calls) == 1
    arguments = calls[0]
    assert arguments == {
        "workspace": tmp_path, "paths": start.app_paths, "config_path": "configuration", "profile": "profile",
        "approval_policy": arguments["approval_policy"], "task_authority_capabilities": ["read", "process"],
        "observability_mode": "normal", "operational_mode": mode, "configure_logging": False,
    }
    if interactive:
        assert arguments["approval_policy"] is approval
    else:
        assert type(arguments["approval_policy"]) is policy
        assert arguments["approval_policy"].request(object()) is (
            ApprovalDecision.APPROVED if automatic else ApprovalDecision.REQUIRED
        )
    assert runtime._owner is owner
    conversation = bind_conversation(runtime)
    assert conversation._session is owner.session is orchestrator.session
    assert _resolve_gateway(conversation) is gateway
    api.close_task_execution(runtime)
    assert closed == [owner] and closed[0] is owner
    with pytest.raises(FrozenInstanceError):
        start.profile = "changed"


@pytest.mark.parametrize("mode,label,write,full", [
    (None, "FULL", True, False),
    (OperationalMode.READ_ONLY, "READ ONLY", False, False),
    (OperationalMode.EDITOR, "EDITOR", True, False),
    (OperationalMode.FULL, "FULL", True, True),
])
def test_live_mode_single_authority(mode, label, write, full):
    from llm_agent.agent.orchestration.operational_modes import OperationalModeMixin

    orchestrator = OperationalModeMixin()
    orchestrator._operational_mode = mode
    runtime = api._retain_runtime(SimpleNamespace(orchestrator=orchestrator))
    assert api.read_execution_capabilities(runtime) == api.TaskExecutionCapabilities(label, write, full)
    orchestrator._operational_mode = OperationalMode.READ_ONLY
    assert api.read_execution_capabilities(runtime).allows_write_validate is False
    assert not hasattr(runtime, "mode")


@pytest.mark.parametrize("directive", ["", "/read", "/plan", "/do"])
@pytest.mark.parametrize("profile", ["/economy", "/normal", "/smart", "/cautious"])
@pytest.mark.parametrize("fallback", [False, True])
def test_dispatch_preserves_prefix_profile_and_subject(directive, profile, fallback):
    text = f"{directive} {profile} inspect source".strip()
    calls = []
    result = SimpleNamespace(answer="ok", status="succeeded", success=True)
    owner = SimpleNamespace(run=lambda *args, **kw: calls.append((args, kw)) or result)
    if not fallback:
        owner.interact = lambda *args, **kw: calls.append((args, kw)) or result
    dispatch = api.prepare_task_dispatch(text)
    assert not dispatch.continues_task and not hasattr(dispatch, "directive")
    settlement = api.execute_submission(api._retain_runtime(owner), text, entry="headless-run", dispatch=dispatch)
    assert calls[0][0] == ("inspect source",)
    if fallback:
        actual = calls[0][1]["task_run_directive"]
        assert actual.subject == "inspect source"
        assert actual.directive.value == (directive.removeprefix("/") or "auto")
        assert actual.deliberation_profile.value == profile[1:]
    else:
        assert calls[0][1] == {"boundary": "task", "visible_user_text": text, "task_payload": text}
    assert api.observe_task_settlement(settlement, channel="interactive")["answer"] == "ok"


def test_binding_same_gateway_dispatcher_and_cleanup_identity():
    order = []
    gateway = SimpleNamespace(approval_port=None)
    dispatcher = SimpleNamespace(add_sink=lambda sink: order.append(("add", sink)),
                                 remove_sink=lambda sink: order.append(("remove", sink)))
    owner = SimpleNamespace(orchestrator=SimpleNamespace(event_dispatcher=dispatcher),
                            tool_invocation_gateway=gateway, approval_policy=None)
    runtime = api._retain_runtime(owner)
    approval, updates = object(), []
    detach = api.bind_interactive_services(runtime, approval, updates.append)
    assert owner.approval_policy is gateway.approval_port is approval
    assert owner.tool_invocation_gateway is gateway
    adapter = order[0][1]
    assert callable(adapter.emit)
    detach()
    assert order == [("add", adapter), ("remove", adapter)]


def test_interactive_binding_projects_canonical_event_and_detaches_same_adapter():
    from llm_agent.application.task_execution import TaskActivityUpdate

    order = []
    dispatcher = SimpleNamespace(add_sink=lambda sink: order.append(("add", sink)),
                                 remove_sink=lambda sink: order.append(("remove", sink)))
    owner = SimpleNamespace(orchestrator=SimpleNamespace(event_dispatcher=dispatcher),
                            tool_invocation_gateway=None, approval_policy=None)
    updates = []
    detach = api.bind_interactive_services(api._retain_runtime(owner), object(), updates.append)
    canonical = RuntimeEvent.from_fields(
        RuntimeEventKind.TOOL_START,
        RunCorrelation.fresh(),
        {"tool": "reader", "invocation_id": "inv-envelope"},
        invocation_id="inv-envelope",
    )
    order[0][1].emit(canonical)
    assert len(updates) == 1 and isinstance(updates[0], TaskActivityUpdate)
    assert updates[0].tool_transition == "start"
    assert updates[0].tool_name == "reader"
    assert updates[0].invocation_id == "inv-envelope"
    assert updates[0] is not canonical
    detach()
    assert order[0][1] is order[1][1]


def test_activity_projector_covers_every_canonical_kind_and_preserves_special_effects():
    from llm_agent.application.task_execution import _project_task_activity_update

    correlation = RunCorrelation.fresh()
    updates = {
        kind: _project_task_activity_update(RuntimeEvent.from_fields(kind, correlation))
        for kind in RuntimeEventKind
    }
    assert len(updates) == len(tuple(RuntimeEventKind))
    milestones = {
        RuntimeEventKind.PLAN_CREATED, RuntimeEventKind.PLAN_EXTENDED,
        RuntimeEventKind.PLAN_PREVIEW_READY, RuntimeEventKind.STEP_COMPLETED,
        RuntimeEventKind.STEP_FAILED, RuntimeEventKind.STEP_BLOCKED,
        RuntimeEventKind.STEP_CANCELLED, RuntimeEventKind.STEP_SKIPPED,
        RuntimeEventKind.STEP_UNVERIFIED, RuntimeEventKind.REPLAN,
        RuntimeEventKind.REPLAN_BLOCKED, RuntimeEventKind.CONVERGENCE_REPLAN_REQUESTED,
        RuntimeEventKind.CONVERGENCE_REPLAN_DENIED, RuntimeEventKind.VALIDATION_REPAIR,
        RuntimeEventKind.TASK_RESUMED, RuntimeEventKind.EXECUTION_FRONTIER_PROJECTED,
        RuntimeEventKind.PROGRESS_RECEIPT_ADVANCED,
    }
    terminals = {RuntimeEventKind.TASK_OUTCOME, RuntimeEventKind.FINAL}
    no_activity = {
        RuntimeEventKind.WARNING, RuntimeEventKind.CONTEXT_REFRESH,
        RuntimeEventKind.OBSERVATION_REUSE, RuntimeEventKind.OBSERVATION_REHYDRATION,
    }
    for kind, update in updates.items():
        expected_delivery = (
            "error" if kind is RuntimeEventKind.ERROR else
            "terminal" if kind in terminals else
            "milestone" if kind in milestones else "latest"
        )
        assert update.delivery == expected_delivery
        assert update.advances_activity is (kind not in no_activity)
        assert update.coalescing_key == kind.value
        assert update.activity == kind.value
        assert update.warning is (kind is RuntimeEventKind.WARNING)
        assert update.terminal_outcome == (kind.value if kind in terminals else None)

    assert updates[RuntimeEventKind.MODEL_CALL_STARTED].model_active is True
    assert updates[RuntimeEventKind.MODEL_CALL_COMPLETED].model_active is False
    assert updates[RuntimeEventKind.TOOL_START].tool_transition == "start"
    assert updates[RuntimeEventKind.TOOL_START].tool_name == "unknown"
    assert updates[RuntimeEventKind.TOOL_START].invocation_id == "unknown"
    assert updates[RuntimeEventKind.TOOL_END].tool_transition == "end"
    assert updates[RuntimeEventKind.TOOL_END].invocation_id == ""
    for kind in {
        RuntimeEventKind.STEP_COMPLETED, RuntimeEventKind.STEP_FAILED,
        RuntimeEventKind.STEP_BLOCKED, RuntimeEventKind.STEP_CANCELLED,
        RuntimeEventKind.STEP_SKIPPED, RuntimeEventKind.STEP_UNVERIFIED,
    }:
        assert updates[kind].step_label == "unknown"

    terminal = _project_task_activity_update(
        RuntimeEvent.from_fields(RuntimeEventKind.TASK_OUTCOME, correlation, {"outcome": "ok"})
    )
    assert terminal.terminal_outcome == "ok"
    tool = _project_task_activity_update(
        RuntimeEvent.from_fields(
            RuntimeEventKind.TOOL_START, correlation,
            {"activity": " ", "phase": "phase", "tool": "reader", "invocation_id": "envelope-id"},
            invocation_id="envelope-id",
        )
    )
    assert (tool.activity, tool.tool_name, tool.invocation_id) == ("phase", "reader", "envelope-id")


@pytest.mark.parametrize("bad", [AutoApprove(), OperationalMode.FULL, SimpleNamespace(), {1: "bad"}])
def test_nested_owner_and_enum_values_rejected(bad):
    settlement = api._settle(SimpleNamespace(to_dict=lambda: {"nested": [{"value": bad}]}))
    with pytest.raises(TypeError):
        api.observe_task_settlement(settlement, channel="headless")


def test_settlement_copy_receipt_diagnostics_and_no_owner_escape():
    receipt = {"tools": [{"tool": "file_reader", "executed": True}]}
    result = SimpleNamespace(answer="ok", error=None, status="succeeded", success=True,
                             interaction_usage={"model_calls": 1}, resolution=SimpleNamespace(action="run"),
                             run_result=SimpleNamespace(receipt=receipt, report_path="report"))
    settlement = api._settle(result)
    observed = api.observe_task_settlement(settlement, channel="interactive")
    assert observed["receipt"] == receipt and observed["receipt"] is not receipt
    assert observed["answer"] == "ok" and observed["resolution"]["action"] == "run"
    assert "run_result" not in observed and "snapshot" not in observed
    observed["receipt"]["tools"].clear()
    assert receipt["tools"]
    for handle in (api.TaskExecutionRuntime, api.TaskDispatch, api.TaskSettlement):
        with pytest.raises(TypeError):
            handle()


def test_cancellation_new_binding_cleanup_and_close_failure():
    from threading import Event

    from llm_agent.agent.application_interactive import InteractiveCancellationMixin

    class Owner(InteractiveCancellationMixin):
        def __init__(self):
            self._closed = False
            self.orchestrator = SimpleNamespace(cancellation_token=SimpleNamespace(cancel=lambda: None))
            self.interaction_service = lambda: SimpleNamespace(cancel_active_model_call=lambda: None)
            self._init_interactive_cancellation()

        def close(self):
            raise RuntimeError("cleanup")

    owner = Owner()
    runtime = api._retain_runtime(owner)
    first, second = Event(), Event()
    callbacks = []
    cleanup = api.bind_submission_cancellation(runtime, first, callbacks.append)
    api.bind_submission_cancellation(runtime, second, callbacks.append)
    cleanup()
    assert owner._interactive_cancellation_event is second
    callbacks[-1]()
    assert second.is_set()
    with pytest.raises(RuntimeError, match="cleanup"):
        try:
            raise ValueError("primary")
        finally:
            api.close_task_execution(runtime)


def test_finite_ancillary_operations_and_primitive_inspection():
    calls = []
    gateway = SimpleNamespace(run=lambda *args, **kwargs: calls.append((args, kwargs)) or
                              SimpleNamespace(to_legacy_dict=lambda: {"ok": True, "data": "observed"}))
    memory = SimpleNamespace(state={"key_findings": {"key": "value"}, "empty": {}})
    orch = SimpleNamespace(tool_invocation_gateway=gateway, allowed_capabilities=frozenset({"read"}),
                           agent_state=SimpleNamespace(memory=memory), context_manager=SimpleNamespace(verbose=False),
                           remember=lambda *args: calls.append(args), clear_memory=lambda: calls.append("clear"))
    runtime = api._retain_runtime(SimpleNamespace(orchestrator=orch))
    assert api.execute_memory_command(runtime, "show") == {"rows": [("key_findings", "{'key': 'value'}")]}
    api.execute_memory_command(runtime, "remember", key="k", value="v")
    api.execute_memory_command(runtime, "clear")
    assert api.execute_workspace_command(runtime, "read", "source")["data"] == "observed"
    assert calls[-1] == (("file_reader", {"file_path": "source"}),
                         {"active_skills": None, "allowed_capabilities": frozenset({"read"})})
    api.set_session_diagnostics(runtime, True)
    assert orch.verbose is orch.context_manager.verbose is True
    with pytest.raises(ValueError):
        api.execute_workspace_command(runtime, "arbitrary", "source")


def test_inspection_observation_has_rendering_parity_and_no_hidden_fields():
    from io import StringIO

    from rich.console import Console

    from llm_agent.interfaces.cli.inspector_rendering import render_snapshot

    data = {name: {} for name in ("heartbeat", "current", "plan_steps", "model_calls", "tools",
                                 "validation", "recovery", "changes", "metrics", "convergence")}
    data.update(run={"run_id": "run", "status": "running", "completeness": "partial",
                     "liveness": {"state": "live"}, "mode": "FULL", "hidden": object()},
                timeline=[{"sequence": 1, "timestamp": "now", "source": "agent", "category": "step",
                           "title": "literal [red]", "status": "ok", "hidden": object()}],
                warnings=[{"sequence": 2, "title": "warning", "summary": "detail", "hidden": object()}],
                selected_detail=None, issues=[], query=object())
    snapshot = SimpleNamespace(**data, to_dict=lambda: data)
    runtime = api._retain_runtime(SimpleNamespace(inspection_service=lambda:
                                 SimpleNamespace(snapshot=lambda **kwargs: snapshot)))
    observation = api.read_runtime_inspection(runtime)
    assert "query" not in observation
    assert "hidden" not in observation["run"]
    assert "hidden" not in observation["timeline"][0]
    assert "hidden" not in observation["warnings"][0]
    outputs = []
    for value in (snapshot, observation):
        output = StringIO()
        render_snapshot(value, Console(file=output, width=100, color_system=None))
        outputs.append(output.getvalue())
    assert outputs[0] == outputs[1]


@pytest.mark.parametrize("private_error,public_error", [
    (api._TraceCorruptError, api.InspectionCorruptDataError),
    (api._TraceUnavailableError, api.InspectionUnavailableError),
])
def test_inspection_errors_cross_only_existing_application_boundary(private_error, public_error):
    def fail(**kwargs):
        raise private_error("trace unavailable")

    runtime = api._retain_runtime(SimpleNamespace(inspection_service=lambda: SimpleNamespace(snapshot=fail)))
    with pytest.raises(public_error, match="trace unavailable"):
        api.read_runtime_inspection(runtime)


def test_close_drain_failure_keeps_original_owner_retryable(monkeypatch):
    from llm_agent.agent import application_lifecycle

    owner = object.__new__(api._AgentApplication)
    owner._closed = False
    owner._task_attempted = True
    owner.tool_invocation_gateway = object()
    owner._instance_lock = object()
    owner._owns_logging = False
    owner._home_lease = None
    events = []

    def drain(gateway):
        assert gateway is owner.tool_invocation_gateway
        events.append("drain")
        if len(events) == 1:
            raise RuntimeError("drain failed")

    monkeypatch.setattr(application_lifecycle, "require_application_invocations_drained", drain)
    monkeypatch.setattr(application_lifecycle, "finish_observation", lambda original: events.append("finish"))
    monkeypatch.setattr(application_lifecycle, "release_resources", lambda *args: events.append("release"))
    runtime = api._retain_runtime(owner)
    with pytest.raises(RuntimeError, match="drain failed"):
        api.close_task_execution(runtime)
    assert not owner._closed and not hasattr(runtime, "_closed")
    api.close_task_execution(runtime)
    assert owner._closed
    api.close_task_execution(runtime)
    assert events == ["drain", "finish", "drain", "finish", "release"]

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent.llm.admitted_decisions import DirectResponseDecision, InitialPlanDecision
from agent.orchestration.task_execution import execute_task
from agent.orchestration.task_runner import TaskRunner
from agent.planning import plan_builder as plan_builder_module
from agent.planning.execution_gateway import ExecutionGateway
from agent.planning.plan_builder import PlanBuilder, PlanBuildResult, PlanningDecisionKind
from agent.planning.plan_model import Plan
from agent.planning.plan_preview import (
    MAX_PREVIEW_STEPS,
    MAX_PREVIEW_TOTAL_CHARS,
    PLAN_PREVIEW_PLAN_REQUIRED,
    PLAN_PREVIEW_VALIDATION_FAILED,
    render_plan_preview,
    run_plan_preview,
)
from agent.planning.plan_prompts import build_plan_prompt
from agent.runtime.task_directives import (
    DeliberationProfile,
    TaskDirective,
    TaskRunDirective,
)
from agent.state import AgentState
from agent.tools.authority import OperationalMode, TaskAuthoritySnapshot
from agent.tools.contracts import ToolDescriptor, ToolInvocation, ToolResult, ToolStatus
from agent.tools.tool_registry import ToolRegistry


class _CancellationToken:
    def reset(self) -> None:
        pass

    def cancel(self) -> None:
        pass


class _CountingAdapter:
    def __init__(self, target) -> None:
        self.target = target
        self.calls = 0
        self._descriptor = ToolDescriptor(
            "file_writer",
            "controlled file writer",
            schema={
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["file_path", "content"],
                "additionalProperties": False,
            },
            capabilities=frozenset({"read", "write"}),
        )

    def descriptors(self) -> tuple[ToolDescriptor, ...]:
        return (self._descriptor,)

    def invoke(self, invocation: ToolInvocation) -> ToolResult:
        self.calls += 1
        self.target.write_text(invocation.args["content"], encoding="utf-8")
        return ToolResult(
            invocation.invocation_id,
            ToolStatus.SUCCEEDED,
            data="written",
            executed=True,
        )


class _PreviewBuilder:
    def __init__(self, result: PlanBuildResult) -> None:
        self.result = result
        self.calls: list[tuple[str, bool]] = []

    def build_plan(
        self,
        objective: str,
        *,
        require_executable_plan: bool = False,
    ) -> PlanBuildResult:
        self.calls.append((objective, require_executable_plan))
        return self.result


class _InterruptingBuilder:
    def build_plan(self, objective: str, *, require_executable_plan: bool = False):
        del objective, require_executable_plan
        raise KeyboardInterrupt()


def _plan(tool: str = "file_writer", args: dict[str, object] | None = None) -> Plan:
    return Plan.from_raw(
        [
            {
                "tool": tool,
                "args": args
                or {"file_path": "target.txt", "content": "MUTATED"},
            }
        ]
    )


def _preview_owner(
    tmp_path,
    *,
    subject: str = "write target.txt",
    candidate: Plan | None = None,
    profile: DeliberationProfile = DeliberationProfile.NORMAL,
    mode: OperationalMode = OperationalMode.FULL,
    task_authority: TaskAuthoritySnapshot | None = None,
):
    target = tmp_path / "target.txt"
    target.write_text("ORIGINAL", encoding="utf-8")
    adapter = _CountingAdapter(target)
    registry = ToolRegistry()
    registry.register_adapter(adapter)
    registry.freeze()

    directive = TaskRunDirective(TaskDirective.PLAN, profile, subject)
    state = AgentState()
    state.objective = directive.canonical_objective()
    state.task_run_directive = directive
    state.initialize_task_semantics(state.objective, plan_only=True)
    events: list[tuple[str, dict[str, object]]] = []
    owner = SimpleNamespace(
        session=SimpleNamespace(messages=[], config={}, thinking_budget=777),
        agent_state=state,
        cancellation_token=_CancellationToken(),
        _cancelled=False,
        _preserve_checkpoint=False,
        _task_failed=False,
        _task_directive_capability_ceiling=None,
        _persona_allowed_capabilities=frozenset(
            {"read", "vcs_read", "analyze", "write", "validate", "process", "network"}
        ),
        _operational_mode=mode,
        task_authority=task_authority,
        tool_registry=registry,
        allowed_capabilities=frozenset(
            {"read", "vcs_read", "analyze", "write", "validate", "process", "network"}
        ),
        skills={},
        active_skills=[],
        verbose=False,
        planning_context=None,
        _emit=lambda event_type, data=None: events.append(
            (event_type, dict(data or {}))
        ),
        _route_persona=lambda _objective: None,
        _save_checkpoint=lambda: True,
        fail_task=lambda: setattr(owner, "_task_failed", True),
    )
    owner.plan_builder = _PreviewBuilder(
        PlanBuildResult(plan=candidate or _plan())
    )
    owner.execution_gateway = ExecutionGateway(owner)
    return owner, adapter, target, directive, events


def test_plan_prompt_explicitly_requires_a_non_executed_candidate_plan() -> None:
    prompt = build_plan_prompt(
        "write target.txt",
        "",
        "file_writer",
        require_executable_plan=True,
    )

    assert "PLAN-ONLY PREVIEW MODE" in prompt
    assert "candidate execution plan" in prompt
    assert "INITIAL_PLAN" in prompt
    assert "NAO vai executar" in prompt
    assert "direct_response" in prompt
    assert "capability grants" in prompt


def test_plan_builder_rejects_direct_response_when_a_plan_is_required(
    monkeypatch,
    tmp_path,
) -> None:
    owner = SimpleNamespace(
        context_manager=SimpleNamespace(),
        agent_state=AgentState(),
        verbose=False,
        _cached_base_prompt=None,
        _log_metric=lambda *_args, **_kwargs: None,
    )
    builder = PlanBuilder(owner, analysis_notes_file=tmp_path / "notes.md")
    builder._build_prompt = lambda *_args, **_kwargs: "preview prompt"
    monkeypatch.setattr(
        plan_builder_module,
        "ask_model_decision_with_compatibility",
        lambda *_args, **_kwargs: DirectResponseDecision("should not be success"),
    )

    result = builder.build_plan("write target.txt", require_executable_plan=True)

    assert result.kind is PlanningDecisionKind.BLOCK
    assert result.blocked_answer == PLAN_PREVIEW_PLAN_REQUIRED


def test_plan_builder_preview_does_not_review_or_install_model_obligations(
    monkeypatch,
    tmp_path,
) -> None:
    subject = "Leia subject.txt."
    directive = TaskRunDirective(TaskDirective.PLAN, DeliberationProfile.NORMAL, subject)
    state = AgentState()
    state.objective = directive.canonical_objective()
    state.task_run_directive = directive
    state.initialize_task_semantics(state.objective, plan_only=True)
    owner = SimpleNamespace(
        context_manager=SimpleNamespace(),
        agent_state=state,
        verbose=False,
        _cached_base_prompt=None,
        _log_metric=lambda *_args, **_kwargs: None,
    )
    builder = PlanBuilder(owner, analysis_notes_file=tmp_path / "notes.md")
    builder._build_prompt = lambda *_args, **_kwargs: "preview prompt"
    obligation = {
        "id": "model-read",
        "kind": "read",
        "target": "subject.txt",
        "description": "Read subject.txt before answering.",
    }
    decision = InitialPlanDecision(
        plan=({"tool": "file_reader", "args": {"file_path": "subject.txt"}},),
        obligations=(obligation,),
    )
    review_calls: list[object] = []

    def forbidden_review(*args: object, **kwargs: object) -> object:
        review_calls.append((args, kwargs))
        raise AssertionError("PLAN-only must not review model obligations")

    state.review_task_obligations_report = forbidden_review  # type: ignore[attr-defined]
    monkeypatch.setattr(
        plan_builder_module,
        "ask_model_decision_with_compatibility",
        lambda *_args, **_kwargs: decision,
    )

    result = builder.build_plan(subject, require_executable_plan=True)

    assert result.kind is PlanningDecisionKind.EXECUTE
    assert result.plan is not None
    assert result.obligations is None
    assert state.pending_obligations() == ()
    assert review_calls == []


def test_ordinary_plan_builder_retains_obligation_review_behavior(
    monkeypatch,
    tmp_path,
) -> None:
    subject = "Leia subject.txt."
    state = AgentState()
    state.initialize_task_semantics(subject)
    owner = SimpleNamespace(
        context_manager=SimpleNamespace(),
        agent_state=state,
        verbose=False,
        _cached_base_prompt=None,
        _log_metric=lambda *_args, **_kwargs: None,
    )
    builder = PlanBuilder(owner, analysis_notes_file=tmp_path / "notes.md")
    builder._build_prompt = lambda *_args, **_kwargs: "ordinary prompt"
    obligation = {
        "id": "model-read",
        "kind": "read",
        "target": "subject.txt",
        "description": "Read subject.txt before answering.",
    }
    decision = InitialPlanDecision(
        plan=({"tool": "file_reader", "args": {"file_path": "subject.txt"}},),
        obligations=(obligation,),
    )
    review_calls: list[tuple[object, str]] = []

    def review(raw: object, *, source: str) -> object:
        review_calls.append((raw, source))
        return SimpleNamespace(
            accepted=(SimpleNamespace(to_dict=lambda: obligation),),
        )

    state.review_task_obligations_report = review  # type: ignore[attr-defined]
    monkeypatch.setattr(
        plan_builder_module,
        "ask_model_decision_with_compatibility",
        lambda *_args, **_kwargs: decision,
    )

    result = builder.build_plan(subject)

    assert result.kind is PlanningDecisionKind.EXECUTE
    assert result.obligations == [obligation]
    assert review_calls == [((obligation,), "initial_plan")]


def test_positive_plan_preview_passes_task_definition_and_never_executes(
    tmp_path,
) -> None:
    owner, adapter, target, directive, events = _preview_owner(tmp_path)
    gateway = owner.execution_gateway
    gateway.execute_validated_plan = lambda *_args, **_kwargs: pytest.fail(
        "PLAN preview must not enter the execution method"
    )
    runner = TaskRunner(owner)
    definition_calls: list[str] = []

    def prepare(inputs) -> None:
        owner.agent_state.objective = inputs.objective
        owner.agent_state.task_run_directive = inputs.task_run_directive
        owner.agent_state.initialize_task_semantics(inputs.objective, plan_only=True)
        from agent.orchestration.task_directive_runtime import (
            apply_task_run_directive_runtime,
        )

        runner._directive_runtime_restore = apply_task_run_directive_runtime(
            owner, inputs.task_run_directive
        )

    runner._prepare = prepare
    runner._ensure_task_definition = (
        lambda inputs: definition_calls.append(inputs.objective) or None
    )
    runner._execute = lambda inputs, on_chunk: execute_task(runner, inputs, on_chunk)
    runner._cleanup = lambda _count: None

    answer = runner.run(directive.subject, None, task_run_directive=directive)

    assert "Validated plan preview" in answer
    assert "No steps were executed." in answer
    assert "write target.txt" in owner.plan_builder.calls[0][0]
    assert owner.plan_builder.calls == [(directive.subject, True)]
    assert definition_calls == [directive.canonical_objective()]
    assert target.read_text(encoding="utf-8") == "ORIGINAL"
    assert adapter.calls == 0
    assert not owner.agent_state.plan
    assert owner.agent_state.terminal_disposition == "complete"
    assert any(event[0] == "plan_preview_ready" for event in events)
    assert not any(event[0] == "plan_created" for event in events)
    assert owner.session.thinking_budget == 777
    assert owner._task_directive_capability_ceiling is None


def test_plan_preview_uses_raw_subject_for_builder_and_validation(tmp_path) -> None:
    subject = "write target.txt"
    owner, _adapter, _target, directive, _events = _preview_owner(tmp_path, subject=subject)
    observed: dict[str, object] = {}

    def validate(candidate, objective, **kwargs):
        observed["candidate"] = candidate
        observed["objective"] = objective
        observed["kwargs"] = kwargs
        return candidate

    owner.execution_gateway = SimpleNamespace(validate_and_optimize_plan=validate)

    answer = run_plan_preview(owner, subject)

    assert "Validated plan preview" in answer
    assert observed["objective"] == subject
    assert owner.plan_builder.calls == [(subject, True)]
    assert observed["kwargs"] == {}
    assert directive.canonical_objective() != subject


def test_invalid_preview_validation_is_canonical_block(tmp_path) -> None:
    owner, adapter, target, _directive, _events = _preview_owner(tmp_path)
    owner.execution_gateway = SimpleNamespace(
        validate_and_optimize_plan=lambda *_args, **_kwargs: None,
        execute_validated_plan=lambda *_args, **_kwargs: pytest.fail("must not execute"),
    )

    answer = run_plan_preview(owner, "write target.txt")

    assert answer
    assert owner.agent_state.last_result["error_code"] == PLAN_PREVIEW_VALIDATION_FAILED
    assert owner.agent_state.terminal_disposition == "block"
    assert not owner.agent_state.plan
    assert target.read_text(encoding="utf-8") == "ORIGINAL"
    assert adapter.calls == 0


@pytest.mark.parametrize(
    "result",
    [
        PlanBuildResult(direct_answer="not a plan"),
        PlanBuildResult(kind=PlanningDecisionKind.REPLAN),
        PlanBuildResult(kind=PlanningDecisionKind.FAIL),
    ],
)
def test_non_plan_planner_decisions_cannot_fall_back_to_reactive(
    tmp_path,
    result: PlanBuildResult,
) -> None:
    owner, _adapter, _target, _directive, _events = _preview_owner(tmp_path)
    owner.plan_builder = _PreviewBuilder(result)
    owner._run_reactive = lambda *_args: pytest.fail("PLAN must not fall back to reactive")

    run_plan_preview(owner, "write target.txt")

    assert owner.agent_state.terminal_disposition == "block"
    assert owner.agent_state.last_result["error_code"] in {
        PLAN_PREVIEW_PLAN_REQUIRED,
        "PLAN_PREVIEW_BUILD_FAILED",
    }


def test_resumed_plan_reenters_preview_before_pending_plan_dispatch(tmp_path) -> None:
    owner, adapter, target, directive, _events = _preview_owner(tmp_path)
    runner = SimpleNamespace(orchestrator=owner)
    owner.execution_gateway.execute_validated_plan = lambda *_args, **_kwargs: pytest.fail(
        "resumed PLAN must not execute a pending plan"
    )

    answer = execute_task(
        runner,
        SimpleNamespace(resumed=True, objective=directive.canonical_objective()),
        None,
    )

    assert "Validated plan preview" in answer
    assert owner.plan_builder.calls == [(directive.subject, True)]
    assert not owner.agent_state.plan
    assert adapter.calls == 0
    assert target.read_text(encoding="utf-8") == "ORIGINAL"


def test_forged_plan_directive_with_executable_plan_fails_closed(tmp_path) -> None:
    owner, _adapter, _target, _directive, _events = _preview_owner(tmp_path)
    owner.agent_state.set_plan(_plan())
    runner = SimpleNamespace(orchestrator=owner)

    answer = execute_task(runner, SimpleNamespace(resumed=True), None)

    assert "PLAN_PREVIEW_EXECUTABLE_PLAN_PRESENT" in str(
        owner.agent_state.last_result["error_code"]
    )
    assert answer


def test_auto_and_do_keep_the_normal_execution_gateway_path(tmp_path) -> None:
    del tmp_path
    state = AgentState()
    state.task_run_directive = TaskRunDirective(
        TaskDirective.AUTO, DeliberationProfile.NORMAL, "objective"
    )
    builder = _PreviewBuilder(PlanBuildResult(plan=_plan("file_reader", {})))
    execute_calls: list[bool] = []
    owner = SimpleNamespace(
        agent_state=state,
        _route_persona=lambda _objective: None,
        _save_checkpoint=lambda: True,
        plan_builder=builder,
        execution_gateway=SimpleNamespace(),
    )
    runner = SimpleNamespace(
        orchestrator=owner,
        _try_hierarchical=lambda *_args: SimpleNamespace(
            disposition="not_applicable", route="hierarchical"
        ),
        _try_security=lambda *_args: SimpleNamespace(
            disposition="not_applicable", route="security"
        ),
        _consume_route_result=lambda *_args, **_kwargs: None,
        _execute_plan=lambda *_args, **_kwargs: execute_calls.append(True) or "executed",
    )

    assert (
        execute_task(runner, SimpleNamespace(resumed=False, objective="objective"), None)
        == "executed"
    )
    assert execute_calls == [True]
    assert builder.calls == [("objective", False)]


@pytest.mark.parametrize(
    ("subject", "profile", "tool", "args"),
    [
        ("write target.txt --yes", DeliberationProfile.NORMAL, "file_writer", {"x": "ignored"}),
        ("write target.txt", DeliberationProfile.NORMAL, "process", {"command": "mutate"}),
        ("execute shell for validation", DeliberationProfile.CAUTIOUS, "process", {"command": "mutate"}),
    ],
)
def test_plan_adversarial_inputs_remain_preview_only(
    tmp_path,
    subject: str,
    profile: DeliberationProfile,
    tool: str,
    args: dict[str, str],
) -> None:
    candidate = _plan(tool, args)
    owner, adapter, target, _directive, _events = _preview_owner(
        tmp_path,
        subject=subject,
        candidate=candidate,
        profile=profile,
        mode=OperationalMode.FULL,
        task_authority=TaskAuthoritySnapshot(
            frozenset({"read", "write", "process", "network", "validate"})
        ),
    )
    owner.execution_gateway = SimpleNamespace(
        validate_and_optimize_plan=lambda candidate, _objective, **_kwargs: candidate,
        execute_validated_plan=lambda *_args, **_kwargs: pytest.fail("must not execute"),
    )

    answer = run_plan_preview(owner, subject)

    assert "No steps were executed." in answer
    assert not owner.agent_state.plan
    assert adapter.calls == 0
    assert target.read_text(encoding="utf-8") == "ORIGINAL"


def test_preview_renderer_redacts_sensitive_and_bounds_arguments() -> None:
    plan = _plan(
        args={
            "file_path": "target.txt",
            "api_key": "super-secret",
            "content": "x" * 1000,
        }
    )

    rendered = render_plan_preview(plan)

    assert "super-secret" not in rendered
    assert "[REDACTED]" in rendered
    assert "No steps were executed." in rendered
    assert len(rendered) < 1200


@pytest.mark.parametrize(
    ("args", "secret"),
    [
        ({"api_key": "API-SECRET"}, "API-SECRET"),
        ({"content": "password=PASSWORD-SECRET"}, "PASSWORD-SECRET"),
        ({"command": "curl -H 'Authorization: Bearer BEARER-SECRET'"}, "BEARER-SECRET"),
        ({"url": "https://example.test/?token=URL-SECRET"}, "URL-SECRET"),
        ({"nested": {"credentials": "NESTED-SECRET"}}, "NESTED-SECRET"),
    ],
)
def test_preview_renderer_uses_canonical_redaction_for_keys_and_embedded_credentials(
    args: dict[str, object],
    secret: str,
) -> None:
    rendered = render_plan_preview(_plan(args=args))

    assert secret not in rendered
    assert "No steps were executed." in rendered


def test_preview_renderer_applies_truthful_total_bound_without_mutating_plan() -> None:
    plan = Plan.from_raw(
        [
            {
                "tool": "file_reader",
                "args": {"file_path": f"file-{index}.txt", "detail": "x" * 500},
                "_step_id": f"step-{index}",
            }
            for index in range(MAX_PREVIEW_STEPS + 8)
        ]
    )
    before = plan.to_dict()

    rendered = render_plan_preview(plan)

    displayed_steps = [line for line in rendered.splitlines() if line[:1].isdigit() and ". " in line]
    marker = next(
        line for line in rendered.splitlines() if "additional validated steps omitted from preview" in line
    )
    omitted = int(marker.split("... ", 1)[1].split(" ", 1)[0])
    assert len(rendered) <= MAX_PREVIEW_TOTAL_CHARS
    assert omitted == len(plan) - len(displayed_steps)
    assert omitted > 0
    assert "No steps were executed." in rendered
    assert plan.to_dict() == before


def test_pause_persists_plan_directive_without_executable_preview_and_can_resume(
    tmp_path,
) -> None:
    owner, _adapter, _target, directive, _events = _preview_owner(tmp_path)
    checkpoints: list[dict[str, object]] = []
    owner.plan_builder = _InterruptingBuilder()
    owner._save_checkpoint = lambda: checkpoints.append(
        {
            "directive": owner.agent_state.task_run_directive.to_checkpoint_dict(),
            "plan": owner.agent_state.plan.to_dict(),
        }
    ) or True
    runner = TaskRunner(owner)

    def prepare(inputs) -> None:
        owner.agent_state.objective = inputs.objective
        owner.agent_state.task_run_directive = inputs.task_run_directive
        owner.agent_state.initialize_task_semantics(inputs.objective, plan_only=True)
        from agent.orchestration.task_directive_runtime import (
            apply_task_run_directive_runtime,
        )

        runner._directive_runtime_restore = apply_task_run_directive_runtime(
            owner, inputs.task_run_directive
        )

    runner._prepare = prepare
    runner._ensure_task_definition = lambda _inputs: None
    runner._cleanup = lambda _count: None

    answer = runner.run(directive.subject, None, task_run_directive=directive)

    assert "pausada" in answer
    assert checkpoints
    assert all(item["directive"] == directive.to_checkpoint_dict() for item in checkpoints)
    assert all(item["plan"] == [] for item in checkpoints)
    assert owner.session.thinking_budget == 777
    assert owner._task_directive_capability_ceiling is None

    resumed_builder = _PreviewBuilder(PlanBuildResult(plan=_plan()))
    owner.plan_builder = resumed_builder
    resumed_answer = execute_task(
        SimpleNamespace(orchestrator=owner),
        SimpleNamespace(resumed=True, objective=directive.canonical_objective()),
        None,
    )

    assert "Validated plan preview" in resumed_answer
    assert resumed_builder.calls == [(directive.subject, True)]
    assert not owner.agent_state.plan


def test_preview_owner_does_not_rewrite_directive_subject() -> None:
    directive = TaskRunDirective(
        TaskDirective.PLAN,
        DeliberationProfile.NORMAL,
        "write target.txt",
    )
    assert directive.subject == "write target.txt"
    assert directive.canonical_objective().startswith(
        "Propose a validated execution plan"
    )

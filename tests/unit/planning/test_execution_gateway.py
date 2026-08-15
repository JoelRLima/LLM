import pytest

from agent.planning.execution_gateway import ExecutionGateway
from agent.planning.planning_context import PlanningContextError, PlanningContextSnapshot, PlanningTool
from agent.planning.replan import _validate_and_optimize_new_steps
from agent.planning.replan_models import ReplanAction
from agent.state import AgentState
from agent.tools.contracts import ToolOriginKind
from agent.tools.runtime_identity import RuntimeSnapshotIdentity


class _Skill:
    name = "echo"

    def get_schema(self):
        return {}


class _PlanExecutor:
    def __init__(self):
        self.calls = 0

    def execute(self, objective, tool_usage_count):
        self.calls += 1
        return None


class _State:
    def __init__(self):
        self.plan = []
        self.tool_history = []

    def set_plan(self, plan):
        self.plan = plan


class _Orchestrator:
    def __init__(self):
        self.skills = {"echo": _Skill()}
        self.active_skills = ["echo"]
        self.agent_state = _State()
        self.plan_executor = _PlanExecutor()
        self.verbose = False
        self.failed = False
        self.events = []

    def fail_task(self):
        self.failed = True

    def _emit(self, event_type, data=None):
        self.events.append((event_type, data or {}))


class _ContextOrchestrator(_Orchestrator):
    def __init__(self):
        super().__init__()
        self.skills = {}
        self.active_skills = []
        self.allowed_capabilities = frozenset({"read"})
        self.tool_registry = None
        self._context = PlanningContextSnapshot(
            snapshot_id="ctx-1",
            registry_identity="registry-1",
            authority_identity="authority-1",
            tools=(
                PlanningTool(
                    name="external",
                    description="external",
                    required_capabilities=frozenset({"read"}),
                    origin_kind=ToolOriginKind.EXTENSION,
                    extension_id="scanner.extension",
                ),
            ),
            eligible_names=frozenset({"external"}),
            runtime_identity=RuntimeSnapshotIdentity("registry-1", "workspace"),
        )

    @property
    def planning_context(self):
        return self._context

    def get_planning_view(self, planner_kind):
        return self._context.present(planner_kind)


def test_gateway_validates_before_execution():
    orchestrator = _Orchestrator()
    gateway = ExecutionGateway(orchestrator)

    result = gateway.execute_validated_plan(
        [{"tool": "echo", "args": {}}],
        "objetivo",
        {},
    )

    assert result.aborted is False
    assert orchestrator.plan_executor.calls == 1
    assert orchestrator.agent_state.plan == result.validated_plan


def test_gateway_does_not_execute_invalid_plan():
    orchestrator = _Orchestrator()
    gateway = ExecutionGateway(orchestrator)

    result = gateway.execute_validated_plan([], "objetivo", {})

    assert result.aborted is True
    assert "bloquead" in (result.final_answer or "").casefold()
    assert orchestrator.failed is True
    assert orchestrator.plan_executor.calls == 0


def _deferred_plan(*, reference=1, operator="equals", literal="original"):
    return [
        {"tool": "echo", "args": {}, "_step_id": "observation"},
        {
            "kind": "deferred_condition",
            "observation_ref": reference,
            "predicate": {"op": operator, "value": literal},
            "on_true": {"tool": "echo", "args": {}},
            "on_false": {"waive_effect": "write"},
        },
    ]


def test_gateway_accepts_and_preserves_valid_deferred_condition() -> None:
    orchestrator = _Orchestrator()
    result = ExecutionGateway(orchestrator).execute_validated_plan(
        _deferred_plan(),
        'Se o valor for "original", escreva.',
        {},
    )

    assert result.aborted is False
    assert orchestrator.plan_executor.calls == 1
    assert result.validated_plan[1]["kind"] == "deferred_condition"
    observation_id = result.validated_plan[0]["_step_id"]
    assert observation_id.startswith("step-")
    assert observation_id != "observation"
    assert result.validated_plan[1]["observation_ref"] == observation_id
    assert isinstance(result.validated_plan[1]["observation_ref"], str)

    persisted = AgentState()
    persisted.set_plan(result.validated_plan)
    restored = AgentState()
    restored.from_checkpoint_dict(persisted.to_checkpoint_dict())
    assert restored.plan[1]["observation_ref"] == restored.plan[0]["_step_id"]


@pytest.mark.parametrize(
    ("plan", "objective", "error_fragment"),
    [
        (_deferred_plan(reference=3), 'Se for "original", escreva.', "observation_ref"),
        (
            _deferred_plan(reference="observation"),
            'Se for "original", escreva.',
            "ordinal local",
        ),
        (
            _deferred_plan(operator="contains"),
            'Se for "original", escreva.',
            "somente equals",
        ),
        (
            _deferred_plan(literal="ausente"),
            'Se for "original", escreva.',
            "objetivo original",
        ),
    ],
)
def test_gateway_rejects_invalid_deferred_condition_without_execution(
    plan,
    objective,
    error_fragment,
) -> None:
    orchestrator = _Orchestrator()
    result = ExecutionGateway(orchestrator).execute_validated_plan(plan, objective, {})

    assert result.aborted is True
    assert orchestrator.plan_executor.calls == 0
    hard_block = next(data for event, data in orchestrator.events if event == "hard_block")
    assert error_fragment in str(hard_block["errors"])


def test_context_validation_does_not_execute_extension() -> None:
    orchestrator = _ContextOrchestrator()
    gateway = ExecutionGateway(orchestrator)

    plan = gateway.validate_and_optimize_plan(
        [{"tool": "external", "args": {}}],
        "objetivo",
    )

    assert plan == [{"tool": "external", "args": {}}]
    assert orchestrator.plan_executor.calls == 0


def test_explicit_context_derives_view_from_context_not_orchestrator() -> None:
    orchestrator = _ContextOrchestrator()
    context_b = PlanningContextSnapshot(
        snapshot_id="ctx-b",
        registry_identity="registry-b",
        authority_identity="authority-b",
        tools=(PlanningTool(name="other", description="other"),),
        eligible_names=frozenset({"other"}),
        runtime_identity=RuntimeSnapshotIdentity("registry-b", "workspace-b"),
    )
    view_b = context_b.present("linear")
    plan = ExecutionGateway(orchestrator).validate_and_optimize_plan(
        [{"tool": "other", "args": {}}],
        "objetivo",
        planning_context=context_b,
        planning_view=view_b,
    )
    assert plan == [{"tool": "other", "args": {}}]


def test_explicit_divergent_context_requires_correlated_view() -> None:
    orchestrator = _ContextOrchestrator()
    orchestrator.active_skills = ["external"]
    context_b = PlanningContextSnapshot(
        snapshot_id="ctx-b2",
        registry_identity="registry-b2",
        authority_identity="authority-b2",
        tools=(PlanningTool(name="other", description="other"),),
        eligible_names=frozenset({"other"}),
        runtime_identity=RuntimeSnapshotIdentity("registry-b2", "workspace-b"),
    )
    gateway = ExecutionGateway(orchestrator)
    with pytest.raises(PlanningContextError):
        gateway.validate_and_optimize_plan(
            [{"tool": "other", "args": {}}], "objetivo", planning_context=context_b
        )
    view_b = context_b.present("linear")
    assert gateway.validate_and_optimize_plan(
        [{"tool": "other", "args": {}}],
        "objetivo",
        planning_context=context_b,
        planning_view=view_b,
    ) == [{"tool": "other", "args": {}}]


def test_same_runtime_identity_but_different_context_snapshot_requires_view() -> None:
    orchestrator = _ContextOrchestrator()
    shared = RuntimeSnapshotIdentity("shared-registry", "workspace")
    orchestrator._context = PlanningContextSnapshot(
        snapshot_id="ctx-a-shared",
        registry_identity=shared.snapshot_id,
        authority_identity="authority-a",
        tools=(PlanningTool(name="safe", description="safe"),),
        eligible_names=frozenset({"safe"}),
        runtime_identity=shared,
    )
    context_b = PlanningContextSnapshot(
        snapshot_id="ctx-b-shared",
        registry_identity=shared.snapshot_id,
        authority_identity="authority-b",
        tools=(PlanningTool(name="other", description="other"),),
        eligible_names=frozenset({"other"}),
        runtime_identity=shared,
    )
    with pytest.raises(PlanningContextError):
        ExecutionGateway(orchestrator).validate_and_optimize_plan(
            [{"tool": "other", "args": {}}], "objetivo", planning_context=context_b
        )


def test_replan_explicit_divergent_context_without_view_fails_typed() -> None:
    orchestrator = _ContextOrchestrator()
    context_b = PlanningContextSnapshot(
        snapshot_id="ctx-b-replan",
        registry_identity="registry-b-replan",
        authority_identity="authority-b-replan",
        tools=(PlanningTool(name="other", description="other"),),
        eligible_names=frozenset({"other"}),
        runtime_identity=RuntimeSnapshotIdentity("registry-b-replan", "workspace-b"),
    )
    with pytest.raises(PlanningContextError):
        _validate_and_optimize_new_steps(
            ReplanAction(steps=[{"tool": "other", "args": {}}], reason="probe"),
            orchestrator,
            context_b,
        )


def test_replan_accepts_only_correlated_view_and_rejects_other_context() -> None:
    orchestrator = _ContextOrchestrator()
    context_b = PlanningContextSnapshot(
        snapshot_id="ctx-b-replan-view",
        registry_identity="registry-b-replan-view",
        authority_identity="authority-b-replan-view",
        tools=(PlanningTool(name="other", description="other"),),
        eligible_names=frozenset({"other"}),
        runtime_identity=RuntimeSnapshotIdentity("registry-b-replan-view", "workspace-b"),
    )
    view_b = context_b.present("linear")
    accepted = _validate_and_optimize_new_steps(
        ReplanAction(steps=[{"tool": "other", "args": {}}], reason="probe"),
        orchestrator,
        context_b,
        view_b,
    )
    assert accepted is not None
    assert accepted.steps == [{"tool": "other", "args": {}}]
    with pytest.raises(PlanningContextError):
        _validate_and_optimize_new_steps(
            ReplanAction(steps=[{"tool": "other", "args": {}}], reason="probe"),
            orchestrator,
            context_b,
            orchestrator._context.present("linear"),
        )


def test_replan_rejects_incompatible_planner_kind() -> None:
    orchestrator = _ContextOrchestrator()
    context_b = PlanningContextSnapshot(
        snapshot_id="ctx-b-replan-kind",
        registry_identity="registry-b-replan-kind",
        authority_identity="authority-b-replan-kind",
        tools=(PlanningTool(name="other", description="other"),),
        eligible_names=frozenset({"other"}),
        runtime_identity=RuntimeSnapshotIdentity("registry-b-replan-kind", "workspace-b"),
    )
    with pytest.raises(PlanningContextError):
        _validate_and_optimize_new_steps(
            ReplanAction(steps=[{"tool": "other", "args": {}}], reason="probe"),
            orchestrator,
            context_b,
            context_b.present("hierarchical"),
        )


def test_replan_rejects_explicit_context_without_view_for_same_runtime_snapshot() -> None:
    orchestrator = _ContextOrchestrator()
    shared = RuntimeSnapshotIdentity("shared-replan", "workspace")
    orchestrator._context = PlanningContextSnapshot(
        snapshot_id="ctx-a-replan-shared",
        registry_identity=shared.snapshot_id,
        authority_identity="authority-a",
        tools=(PlanningTool(name="only_a", description="a"),),
        eligible_names=frozenset({"only_a"}),
        runtime_identity=shared,
    )
    context_b = PlanningContextSnapshot(
        snapshot_id="ctx-b-replan-shared",
        registry_identity=shared.snapshot_id,
        authority_identity="authority-b",
        tools=(PlanningTool(name="only_b", description="b"),),
        eligible_names=frozenset({"only_b"}),
        runtime_identity=shared,
    )
    with pytest.raises(PlanningContextError):
        _validate_and_optimize_new_steps(
            ReplanAction(steps=[{"tool": "only_b", "args": {}}], reason="probe"),
            orchestrator,
            context_b,
        )

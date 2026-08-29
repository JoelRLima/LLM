from types import SimpleNamespace

import pytest

import agent.planning.plan_admission as admission_module
from agent.planning.plan_admission import (
    PlanAdmissionMode,
    PlanAdmissionService,
)
from agent.planning.plan_model import Plan, ToolPlanStep, deserialize_plan, serialize_plan
from agent.planning.plan_validation_types import ValidationReport


def _plan(*step_ids: str) -> Plan:
    return Plan.from_raw(
        [{"_step_id": step_id, "tool": "reader", "args": {}} for step_id in step_ids]
    )


def _orchestrator() -> SimpleNamespace:
    current = _plan("current")
    history = (
        {"plan_id": "run-a", "step_id": "current"},
        {"plan_id": "run-b", "step_id": "other"},
        {"step_id": "generic"},
    )
    return SimpleNamespace(
        skills={},
        active_skills=[],
        allowed_capabilities=None,
        tool_registry=None,
        agent_state=SimpleNamespace(
            plan=current,
            plan_identity="run-a",
            tool_history=history,
        ),
    )


class _RecordingValidator:
    calls: list[dict[str, object]] = []

    def __init__(self, *args: object, **kwargs: object) -> None:
        del args
        self.options = kwargs
        self.calls.append(kwargs)

    def validate(self, plan: object) -> ValidationReport:
        self.options["validated_plan"] = plan
        return ValidationReport(is_valid=True)

    def _validate_step_schema(self, step: ToolPlanStep) -> str | None:
        self.options["validated_step"] = step
        return "blocked" if step.tool == "blocked" else None


def test_mode_policy_is_explicit_and_matches_the_characterized_paths() -> None:
    service = PlanAdmissionService(_orchestrator())
    canonical = Plan.from_raw(
        [
            {"_step_id": "current", "tool": "reader", "args": {}},
            {
                "_step_id": "consumer",
                "tool": "reader",
                "args": {},
                "bindings": {"source": {"from_step": "current", "path": []}},
            },
        ]
    )

    assert service.policy_for(PlanAdmissionMode.INITIAL, canonical).canonical_deferred_references is False
    assert service.policy_for(PlanAdmissionMode.BOUND, canonical).canonical_deferred_references is True
    assert service.policy_for(PlanAdmissionMode.POST_OPTIMIZATION, canonical).canonical_deferred_references is True
    assert service.policy_for(PlanAdmissionMode.REPLAN, canonical).canonical_deferred_references is False
    assert service.policy_for(PlanAdmissionMode.VALIDATION_REPAIR, canonical).canonical_deferred_references is True
    assert service.policy_for(PlanAdmissionMode.MATERIALIZED_DEFERRED, canonical).canonical_deferred_references is True
    assert service.policy_for(
        PlanAdmissionMode.INITIAL,
        canonical,
        allow_conditional_preview=True,
    ).allow_conditional_preview is True
    assert service.policy_for(
        PlanAdmissionMode.REPLAN,
        canonical,
        allow_conditional_preview=True,
    ).allow_conditional_preview is False


def test_observation_scope_isolated_for_fresh_and_matching_plans() -> None:
    service = PlanAdmissionService(_orchestrator())
    matching_policy = service.policy_for(PlanAdmissionMode.INITIAL, _plan("current"))
    matching = service.observation_scope(_plan("current"), matching_policy)
    fresh_policy = service.policy_for(PlanAdmissionMode.INITIAL, _plan("fresh"))
    fresh = service.observation_scope(_plan("fresh"), fresh_policy)

    assert matching[1] == "run-a"
    assert matching[0] == (
        {"plan_id": "run-a", "step_id": "current"},
        {"step_id": "generic"},
    )
    assert fresh == ((), None)


def test_generated_id_coincidence_cannot_grant_observation_scope() -> None:
    current = Plan.from_raw([{"tool": "reader", "args": {}}])
    state = SimpleNamespace(
        plan=current,
        plan_identity="plan-A",
        tool_history=({"plan_id": "plan-A", "step_id": current.steps[0].step_id},),
    )
    orchestrator = _orchestrator()
    orchestrator.agent_state = state
    service = PlanAdmissionService(orchestrator)
    fresh = Plan.from_raw([{"tool": "writer", "args": {}}])
    policy = service.policy_for(PlanAdmissionMode.REPLAN, fresh)

    assert service.observation_scope(fresh, policy) == ((), None)


def test_serialized_typed_copy_preserves_scope_identity() -> None:
    service = PlanAdmissionService(_orchestrator())
    copied = deserialize_plan(serialize_plan(_plan("current")))
    policy = service.policy_for(PlanAdmissionMode.BOUND, copied)

    observations, plan_identity = service.observation_scope(copied, policy)

    assert plan_identity == "run-a"
    assert observations[0]["plan_id"] == "run-a"


def test_materialized_mode_uses_active_plan_scope_without_candidate_overlap() -> None:
    service = PlanAdmissionService(_orchestrator())
    candidate = _plan("new-materialized-step")
    policy = service.policy_for(PlanAdmissionMode.MATERIALIZED_DEFERRED, candidate)

    observations, plan_identity = service.observation_scope(candidate, policy)

    assert plan_identity == "run-a"
    assert observations == (
        {"plan_id": "run-a", "step_id": "current"},
        {"step_id": "generic"},
    )


def test_admit_composes_one_validator_with_the_mode_and_scoped_inputs(monkeypatch: pytest.MonkeyPatch) -> None:
    _RecordingValidator.calls.clear()
    monkeypatch.setattr(admission_module, "PlanValidator", _RecordingValidator)
    service = PlanAdmissionService(_orchestrator())

    report = service.admit(
        _plan("current"),
        "objective",
        mode=PlanAdmissionMode.INITIAL,
        allow_conditional_preview=True,
    )

    assert report.is_valid is True
    options = _RecordingValidator.calls[-1]
    assert options["canonical_deferred_references"] is False
    assert options["plan_identity"] == "run-a"
    assert options["available_observations"] == (
        {"plan_id": "run-a", "step_id": "current"},
        {"step_id": "generic"},
    )
    assert options["allow_conditional_preview"] is True


def test_admit_step_is_only_for_materialized_deferred_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    _RecordingValidator.calls.clear()
    monkeypatch.setattr(admission_module, "PlanValidator", _RecordingValidator)
    service = PlanAdmissionService(_orchestrator())
    step = ToolPlanStep(step_id="materialized", tool="reader", args={})

    assert service.admit_step(
        step,
        "objective",
        mode=PlanAdmissionMode.MATERIALIZED_DEFERRED,
    ) is None
    with pytest.raises(ValueError):
        service.admit_step(step, "objective", mode=PlanAdmissionMode.REPLAN)

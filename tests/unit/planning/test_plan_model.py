from __future__ import annotations

from copy import deepcopy

import pytest

from agent.llm.admitted_decisions import InitialPlanDecision, admit_typed_model_decision
from agent.llm.decision_contract import ModelRequestContract, admit_model_decision_value
from agent.planning.deferred_condition import validate_deferred_condition
from agent.planning.plan_model import (
    DeferredConditionStep,
    Plan,
    PlanDecodeError,
    PlanReferenceError,
    PlanStepReference,
    ToolPlanStep,
    bind_plan_references,
    deserialize_plan,
    resolve_deferred_observation_reference,
    resolve_previous_step_reference,
    resolve_result_binding_reference,
    serialize_plan,
)


def _source_consumer(binding: object) -> list[dict[str, object]]:
    return [
        {"tool": "source", "args": {}, "_step_id": "source"},
        {
            "tool": "sink",
            "args": {"fixed": "kept"},
            "bindings": {"value": binding},
            "_step_id": "sink",
        },
    ]


def _deferred_plan(reference: object) -> list[dict[str, object]]:
    return [
        {"tool": "source", "args": {}, "_step_id": "source"},
        {
            "kind": "deferred_condition",
            "observation_ref": reference,
            "predicate": {"op": "equals", "value": "ready"},
            "on_true": {"tool": "sink", "args": {}},
            "on_false": {"waive_effect": "write"},
            "_step_id": "condition",
        },
    ]


def test_admitted_plan_decodes_to_immutable_typed_plan() -> None:
    decision = admit_typed_model_decision(
        {
            "action": "use_tools",
            "plan": [{"tool": "source", "args": {"nested": [1]}}],
        },
        request_contract=ModelRequestContract.INITIAL_PLAN,
    )
    assert isinstance(decision, InitialPlanDecision)

    plan = Plan.from_decision(decision, new_step_id=lambda: "source")

    assert isinstance(plan.steps[0], ToolPlanStep)
    assert plan.steps[0].step_id == "source"
    assert plan.steps[0].args["nested"] == (1,)
    with pytest.raises(TypeError):
        plan.steps[0].args["new"] = "blocked"  # type: ignore[index]


def test_result_and_deferred_references_share_one_target_resolver() -> None:
    result_plan = Plan.from_raw(
        _source_consumer({"from_step": 1, "path": []})
    )
    deferred_plan = Plan.from_raw(_deferred_plan(1))

    result_step = result_plan.steps[1]
    deferred_step = deferred_plan.steps[1]
    assert isinstance(result_step, ToolPlanStep)
    assert isinstance(deferred_step, DeferredConditionStep)
    assert resolve_result_binding_reference(
        result_step.bindings["value"], 1, result_plan  # type: ignore[index]
    ) == resolve_deferred_observation_reference(deferred_step, 1, deferred_plan) == 0


@pytest.mark.parametrize(
    "reference",
    [
        {"from_step": 0, "path": []},
        {"from_step": {"ordinal": 1}, "path": []},
    ],
)
def test_invalid_result_reference_shapes_fail_closed(reference) -> None:
    with pytest.raises(PlanDecodeError):
        Plan.from_raw(_source_consumer(reference))


def test_first_step_cannot_reference_ordinal_one_as_previous() -> None:
    plan = Plan.from_raw(
        [
            {
                "tool": "source",
                "args": {},
                "bindings": {"value": {"from_step": 1, "path": []}},
                "_step_id": "source",
            }
        ]
    )
    with pytest.raises(PlanReferenceError):
        bind_plan_references(plan)


def test_future_and_missing_stable_references_fail_closed() -> None:
    future = Plan.from_raw(
        _source_consumer({"from_step": 3, "path": []})
    )
    missing = Plan.from_raw(
        _source_consumer({"from_step": "not-present", "path": []})
    )
    with pytest.raises(PlanReferenceError):
        bind_plan_references(future)
    with pytest.raises(PlanReferenceError):
        bind_plan_references(missing)


def test_duplicate_stable_step_id_is_rejected() -> None:
    with pytest.raises(PlanDecodeError):
        Plan.from_raw(
            [
                {"tool": "source", "args": {}, "_step_id": "same"},
                {"tool": "sink", "args": {}, "_step_id": "same"},
            ]
        )


def test_path_zero_and_content_are_valid_but_negative_index_is_not() -> None:
    valid = Plan.from_raw(_source_consumer({"from_step": 1, "path": [0, "content"]}))
    assert valid.steps[1].bindings["value"].path == (0, "content")  # type: ignore[index]
    with pytest.raises(PlanDecodeError):
        Plan.from_raw(_source_consumer({"from_step": 1, "path": [-1]}))


def test_raw_mutation_cannot_change_plan_truth() -> None:
    raw = _source_consumer({"from_step": 1, "path": []})
    raw[0]["args"] = {"nested": ["before"]}
    plan = Plan.from_raw(raw)
    raw[0]["args"]["nested"][0] = "after"  # type: ignore[index]
    assert plan.steps[0].args["nested"] == ("before",)


def test_canonical_serialization_restore_is_exact_and_detached() -> None:
    original = bind_plan_references(
        Plan.from_raw(_source_consumer({"from_step": 1, "path": [0, "content"]}))
    )
    serialized = serialize_plan(original)
    restored = deserialize_plan(serialized)

    assert restored == original
    serialized[0]["args"]["mutated"] = True
    assert "mutated" not in restored.steps[0].args


def test_canonical_bind_is_idempotent() -> None:
    original = Plan.from_raw(_source_consumer({"from_step": 1, "path": []}))
    once = bind_plan_references(original)
    twice = bind_plan_references(once)

    assert once == twice
    assert once.steps[1].bindings["value"].from_step.is_stable_id  # type: ignore[index]
    assert once.steps[1].bindings["value"].from_step.step_id == "source"  # type: ignore[index]


def test_reorder_after_canonical_binding_cannot_retarget() -> None:
    bound = bind_plan_references(
        Plan.from_raw(_source_consumer({"from_step": 1, "path": []}))
    )
    reordered = Plan((bound.steps[1], bound.steps[0]))

    with pytest.raises(PlanReferenceError):
        bind_plan_references(reordered)


def test_checkpoint_shape_unknown_fields_and_non_mapping_steps_fail_closed() -> None:
    with pytest.raises(PlanDecodeError):
        deserialize_plan([{"tool": "source", "args": {}, "unknown": True}])
    with pytest.raises(PlanDecodeError):
        deserialize_plan([{"tool": "source", "args": []}])


def test_injected_default_ids_are_deterministic_without_aliasing() -> None:
    raw = deepcopy(_source_consumer({"from_step": 1, "path": []}))
    raw[0].pop("_step_id")
    raw[1].pop("_step_id")
    def factory():
        values = iter(("step-1", "step-2"))
        return lambda: next(values)

    first = deserialize_plan(raw, new_step_id=factory())
    second = deserialize_plan(raw, new_step_id=factory())
    assert [step.step_id for step in first] == ["step-1", "step-2"]
    assert first == second


def test_independent_generated_plan_ids_are_collision_resistant() -> None:
    first = Plan.from_raw([{"tool": "reader", "args": {}}])
    second = Plan.from_raw([{"tool": "writer", "args": {}}])

    assert first.steps[0].step_id != second.steps[0].step_id


def test_deferred_branch_bindings_survive_typed_decode_until_domain_rejection() -> None:
    raw = {
        "action": "use_tools",
        "plan": [
            {"tool": "source", "args": {}},
            {
                "kind": "deferred_condition",
                "observation_ref": 1,
                "predicate": {"op": "equals", "value": "ready"},
                "on_true": {
                    "tool": "sink",
                    "args": {},
                    "bindings": {"x": {"from_step": 1, "path": []}},
                },
                "on_false": {"waive_effect": "write"},
            },
        ],
    }

    assert admit_model_decision_value(
        raw, request_contract=ModelRequestContract.INITIAL_PLAN
    ) == raw
    decision = admit_typed_model_decision(
        raw, request_contract=ModelRequestContract.INITIAL_PLAN
    )
    assert isinstance(decision, InitialPlanDecision)
    plan = Plan.from_decision(decision)

    assert plan.to_dict()[1]["on_true"]["bindings"] == raw["plan"][1]["on_true"]["bindings"]
    assert validate_deferred_condition(plan.steps[1], 1, plan, "ready") == (
        "on_true deve conter somente tool e args"
    )


def test_direct_resolver_requires_previous_slot() -> None:
    plan = Plan.from_raw(_source_consumer({"from_step": 1, "path": []}))
    assert resolve_previous_step_reference(
        PlanStepReference.from_ordinal(1), 1, plan
    ) == 0
    with pytest.raises(PlanReferenceError):
        resolve_previous_step_reference(PlanStepReference.from_ordinal(2), 1, plan)

from __future__ import annotations

import pytest

from llm_agent.agent.llm.admitted_decisions import admit_typed_model_decision
from llm_agent.agent.llm.decision_contract import (
    ModelRequestContract,
    admit_model_decision_value,
)
from llm_agent.agent.llm.grammars import get_grammar
from llm_agent.agent.planning.plan_model import (
    DeferredConditionStep,
    Plan,
    PlanDecodeError,
    ToolPlanStep,
    bind_plan_references,
)
from llm_agent.agent.planning.plan_optimizer import PlanOptimizer


def test_associations_round_trip_full_plan_and_deferred_checkpoint() -> None:
    plan = Plan.from_raw(
        [
            {
                "tool": "observe",
                "args": {},
                "requirement_ids": ["req-1"],
                "obligation_ids": ["obl-1"],
                "_step_id": "observe",
            },
            {
                "kind": "deferred_condition",
                "observation_ref": 1,
                "predicate": {"op": "equals", "value": "ready"},
                "on_true": {"tool": "write", "args": {}},
                "on_false": {"waive_effect": "write"},
                "requirement_ids": ["req-2"],
                "obligation_ids": ["obl-2"],
                "_step_id": "condition",
            },
        ]
    )

    restored = Plan.from_raw(bind_plan_references(plan).to_checkpoint())

    assert isinstance(restored[0], ToolPlanStep)
    assert isinstance(restored[1], DeferredConditionStep)
    assert restored[0].requirement_ids == ("req-1",)
    assert restored[0].obligation_ids == ("obl-1",)
    assert restored[1].requirement_ids == ("req-2",)
    assert restored[1].obligation_ids == ("obl-2",)
    assert "requirement_ids" not in restored.to_dict()[1]["on_true"]
    assert restored.to_checkpoint() == bind_plan_references(plan).to_checkpoint()


def test_missing_associations_keep_legacy_serialization_shape() -> None:
    plan = Plan.from_raw([{"tool": "echo", "args": {}, "_step_id": "s1"}])

    assert plan.to_dict() == [{"tool": "echo", "args": {}, "_step_id": "s1"}]


def test_optimizer_does_not_merge_steps_with_different_associations() -> None:
    left = ToolPlanStep("s1", "echo", {}, requirement_ids=("r-1",))
    right = ToolPlanStep("s2", "echo", {}, requirement_ids=("r-2",))

    assert PlanOptimizer._step_key(left) != PlanOptimizer._step_key(right)


@pytest.mark.parametrize(
    "invalid",
    [
        "req-1",
        True,
        None,
        [""],
        ["  "],
        ["x" * 129],
        ["same", "same"],
        [],
        [f"req-{index}" for index in range(17)],
        [1],
    ],
)
@pytest.mark.parametrize("field", ["requirement_ids", "obligation_ids"])
def test_plan_decode_rejects_malformed_or_unbounded_associations(field, invalid) -> None:
    with pytest.raises(PlanDecodeError):
        Plan.from_raw([{"tool": "echo", "args": {}, field: invalid}])


@pytest.mark.parametrize(
    ("contract", "action"),
    [
        (ModelRequestContract.INITIAL_PLAN, "use_tools"),
        (ModelRequestContract.EFFECT_OBSERVATION_CONTINUATION, "execute"),
        (ModelRequestContract.REASONING_BOUNDARY_CONTINUATION, "execute"),
    ],
)
def test_plan_contracts_admit_and_project_bounded_association_claims(contract, action) -> None:
    raw = {
        "action": action,
        "plan": [
            {
                "tool": "echo",
                "args": {},
                "requirement_ids": ["r-1", "r-2"],
                "obligation_ids": ["o-1"],
            }
        ],
    }
    if contract is ModelRequestContract.INITIAL_PLAN:
        raw["obligations"] = []

    admitted = admit_model_decision_value(raw, request_contract=contract)
    typed = admit_typed_model_decision(raw, request_contract=contract)

    assert admitted == raw
    assert typed is not None
    assert typed.to_dict() == raw


@pytest.mark.parametrize(
    "invalid",
    ["r-1", True, None, [], [""], ["x" * 129], ["dup", "dup"], [f"r{i}" for i in range(17)]],
)
@pytest.mark.parametrize(
    "contract",
    [
        ModelRequestContract.INITIAL_PLAN,
        ModelRequestContract.EFFECT_OBSERVATION_CONTINUATION,
        ModelRequestContract.REASONING_BOUNDARY_CONTINUATION,
    ],
)
def test_model_admission_rejects_malformed_association_claims(contract, invalid) -> None:
    action = "use_tools" if contract is ModelRequestContract.INITIAL_PLAN else "execute"
    raw = {"action": action, "plan": [{"tool": "echo", "args": {}, "requirement_ids": invalid}]}
    if contract is ModelRequestContract.INITIAL_PLAN:
        raw["obligations"] = []

    assert admit_model_decision_value(raw, request_contract=contract) is None
    assert admit_typed_model_decision(raw, request_contract=contract) is None


def test_model_admission_rejects_metadata_claims_that_are_not_associations() -> None:
    raw = {
        "action": "use_tools",
        "plan": [{"tool": "echo", "args": {}, "evidence": ["verified"], "status": "succeeded"}],
    }

    assert admit_model_decision_value(
        raw, request_contract=ModelRequestContract.INITIAL_PLAN
    ) is None


def test_deferred_model_metadata_belongs_to_slot_not_materialized_branch() -> None:
    base = {
        "action": "use_tools",
        "plan": [
            {"tool": "observe", "args": {}},
            {
                "kind": "deferred_condition",
                "observation_ref": 1,
                "predicate": {"op": "equals", "value": "ready"},
                "on_true": {"tool": "write", "args": {}},
                "on_false": {"waive_effect": "write"},
                "requirement_ids": ["r-2"],
                "obligation_ids": ["o-2"],
            },
        ],
    }
    assert admit_model_decision_value(
        base, request_contract=ModelRequestContract.INITIAL_PLAN
    ) == base
    malformed = {**base, "plan": [*base["plan"]]}
    malformed["plan"][1] = {
        **malformed["plan"][1],
        "on_true": {"tool": "write", "args": {}, "requirement_ids": ["r-2"]},
    }
    assert admit_model_decision_value(
        malformed, request_contract=ModelRequestContract.INITIAL_PLAN
    ) is None


@pytest.mark.parametrize(
    "contract",
    [
        ModelRequestContract.INITIAL_PLAN,
        ModelRequestContract.EFFECT_OBSERVATION_CONTINUATION,
        ModelRequestContract.REASONING_BOUNDARY_CONTINUATION,
    ],
)
def test_plan_grammars_allow_association_arrays(contract) -> None:
    grammar = get_grammar(request_contract=contract)

    assert grammar is not None
    assert r'\"requirement_ids\"' in grammar
    assert r'\"obligation_ids\"' in grammar

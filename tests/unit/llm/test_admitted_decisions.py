from __future__ import annotations

import pytest

import agent.llm.admitted_decisions as admitted_module
from agent.llm.admitted_decisions import (
    DirectResponseDecision,
    EffectObservationCompleteWithoutEffectDecision,
    EffectObservationExecuteDecision,
    FinalGenerationDecision,
    InitialPlanDecision,
    MacroPlanDecision,
    ReactiveFinalDecision,
    ReactiveToolDecision,
    ReasoningBoundaryCompleteDecision,
    ReplanDecision,
    SummarizationDecision,
    ToolDiscoveryDecision,
    admit_typed_model_decision,
    ask_typed_model_decision,
)
from agent.llm.contracts import ModelMessage, ModelRequest, ModelResponse
from agent.llm.decision_contract import ModelRequestContract
from agent.llm.structured_output import resolve_model_decision


@pytest.mark.parametrize(
    ("contract", "value", "expected"),
    [
        (
            ModelRequestContract.INITIAL_PLAN,
            {"action": "use_tools", "plan": [], "obligations": []},
            InitialPlanDecision,
        ),
        (
            ModelRequestContract.INITIAL_PLAN,
            {"action": "direct_response", "answer": "done"},
            DirectResponseDecision,
        ),
        (
            ModelRequestContract.EFFECT_OBSERVATION_CONTINUATION,
            {"action": "execute", "plan": [{"tool": "echo", "args": {}}]},
            EffectObservationExecuteDecision,
        ),
        (
            ModelRequestContract.EFFECT_OBSERVATION_CONTINUATION,
            {"action": "complete_without_effect", "observation_index": 1},
            EffectObservationCompleteWithoutEffectDecision,
        ),
        (
            ModelRequestContract.REASONING_BOUNDARY_CONTINUATION,
            {"action": "complete", "reason": "enough", "obligations": []},
            ReasoningBoundaryCompleteDecision,
        ),
        (
            ModelRequestContract.MACRO_PLAN,
            {"steps": []},
            MacroPlanDecision,
        ),
        (
            ModelRequestContract.REACTIVE_TOOL_DECISION,
            {"action": "tool", "tool": "echo", "args": {}},
            ReactiveToolDecision,
        ),
        (
            ModelRequestContract.REACTIVE_TOOL_DECISION,
            {"action": "final", "answer": "done"},
            ReactiveFinalDecision,
        ),
        (
            ModelRequestContract.REPLAN,
            {"action": "tool", "tool": "echo", "args": {}},
            ReplanDecision,
        ),
        (
            ModelRequestContract.FINAL_GENERATION,
            {"answer": "done"},
            FinalGenerationDecision,
        ),
        (
            ModelRequestContract.SUMMARIZATION,
            {"summary": "done"},
            SummarizationDecision,
        ),
        (
            ModelRequestContract.TOOL_DISCOVERY,
            {"tools": ["echo"]},
            ToolDiscoveryDecision,
        ),
    ],
)
def test_every_contract_projects_to_a_distinct_typed_value(contract, value, expected) -> None:
    decision = admit_typed_model_decision(value, request_contract=contract)

    assert isinstance(decision, expected)
    assert decision is not None
    assert decision.request_contract is contract
    assert decision.to_dict() == value


@pytest.mark.parametrize(
    ("contract", "value"),
    [
        (ModelRequestContract.INITIAL_PLAN, {"plan": []}),
        (ModelRequestContract.INITIAL_PLAN, {"action": "continue_after_plan", "plan": []}),
        (ModelRequestContract.FINAL_GENERATION, {"action": "final", "answer": "done"}),
        (ModelRequestContract.REACTIVE_TOOL_DECISION, {"action": "final"}),
        (ModelRequestContract.EFFECT_OBSERVATION_CONTINUATION, {"action": "complete", "reason": "wrong"}),
        (ModelRequestContract.REASONING_BOUNDARY_CONTINUATION, {"action": "complete_without_effect", "observation_index": 1}),
    ],
)
def test_invalid_or_compatibility_shapes_never_project(contract, value) -> None:
    assert admit_typed_model_decision(value, request_contract=contract) is None


def test_unknown_or_missing_contract_fails_closed() -> None:
    value = {"action": "final", "answer": "done"}

    assert admit_typed_model_decision(value) is None
    assert admit_typed_model_decision(value, request_contract="not-a-contract") is None


def test_effect_and_reasoning_continuations_cannot_cross_construct() -> None:
    assert admit_typed_model_decision(
        {"action": "complete", "reason": "done"},
        request_contract=ModelRequestContract.EFFECT_OBSERVATION_CONTINUATION,
    ) is None
    assert admit_typed_model_decision(
        {"action": "complete_without_effect", "observation_index": 1},
        request_contract=ModelRequestContract.REASONING_BOUNDARY_CONTINUATION,
    ) is None


def test_replan_and_reactive_tool_have_distinct_typed_owners() -> None:
    value = {"action": "tool", "tool": "echo", "args": {}}

    reactive = admit_typed_model_decision(
        value, request_contract=ModelRequestContract.REACTIVE_TOOL_DECISION
    )
    replan = admit_typed_model_decision(
        value, request_contract=ModelRequestContract.REPLAN
    )

    assert isinstance(reactive, ReactiveToolDecision)
    assert isinstance(replan, ReplanDecision)
    assert reactive.request_contract is not replan.request_contract


def test_nested_model_data_is_detached_and_immutable() -> None:
    value = {
        "action": "tool",
        "tool": "echo",
        "args": {"nested": {"values": ["before"]}},
        "bindings": {"value": {"from_step": 1, "path": [0]}},
    }
    decision = admit_typed_model_decision(
        value, request_contract=ModelRequestContract.REACTIVE_TOOL_DECISION
    )
    assert isinstance(decision, ReactiveToolDecision)

    value["args"]["nested"]["values"][0] = "after"
    value["bindings"]["value"]["path"][0] = 99

    assert decision.args["nested"]["values"][0] == "before"
    assert decision.bindings is not None
    assert decision.bindings["value"].path == (0,)
    with pytest.raises(TypeError):
        decision.args["new"] = "blocked"  # type: ignore[index]


def test_safe_repr_does_not_dump_decision_payload() -> None:
    decision = admit_typed_model_decision(
        {"answer": "secret answer"},
        request_contract=ModelRequestContract.FINAL_GENERATION,
    )

    assert decision is not None
    assert "secret answer" not in repr(decision)


def test_structured_resolver_projects_the_already_admitted_value() -> None:
    request = ModelRequest(
        messages=(ModelMessage(role="user", content="answer"),),
        model="test",
        temperature=0.0,
        max_output_tokens=32,
        request_contract=ModelRequestContract.FINAL_GENERATION,
    )

    result = resolve_model_decision(
        request,
        complete=lambda _request: ModelResponse(content='{"answer":"done"}'),
        retry_request=lambda: request,
        grammar=None,
        grammar_supported=None,
        set_grammar_supported=lambda _value: None,
        request_contract=ModelRequestContract.FINAL_GENERATION,
        typed=True,
    )

    assert isinstance(result, FinalGenerationDecision)
    assert result.answer == "done"


def test_invalid_raw_has_no_public_canonical_projection_bypass() -> None:
    raw = {"answer": "ok", "extra": "invalid"}

    assert admit_typed_model_decision(
        raw, request_contract=ModelRequestContract.FINAL_GENERATION
    ) is None
    assert not hasattr(admitted_module, "project_admitted_model_decision")


@pytest.mark.parametrize("use_alias", [False, True])
def test_typed_request_rejects_cross_contract_custom_context(use_alias: bool) -> None:
    wrong = admit_typed_model_decision(
        {"action": "tool", "tool": "echo", "args": {}},
        request_contract=ModelRequestContract.REACTIVE_TOOL_DECISION,
    )
    assert isinstance(wrong, ReactiveToolDecision)

    class _Context:
        def ask_model_typed(self, *_args, **_kwargs):
            return wrong

    context = _Context()
    target = context if not use_alias else context
    assert ask_typed_model_decision(
        target,
        "answer",
        request_contract=ModelRequestContract.FINAL_GENERATION,
    ) is None


def test_typed_request_accepts_only_the_requested_exact_contract() -> None:
    correct = admit_typed_model_decision(
        {"answer": "ok"}, request_contract=ModelRequestContract.FINAL_GENERATION
    )

    class _Context:
        def ask_model_typed(self, *_args, **_kwargs):
            return correct

    assert ask_typed_model_decision(
        _Context(),
        "answer",
        request_contract=ModelRequestContract.FINAL_GENERATION,
    ) is correct


@pytest.mark.parametrize("obligations", [[], [{"kind": "read"}], ["x"]])
def test_obligations_preserve_every_structurally_admitted_item_shape(obligations) -> None:
    raw = {
        "action": "complete",
        "reason": "ok",
        "obligations": obligations,
    }
    decision = admit_typed_model_decision(
        raw,
        request_contract=ModelRequestContract.REASONING_BOUNDARY_CONTINUATION,
    )

    assert isinstance(decision, ReasoningBoundaryCompleteDecision)
    assert decision.to_dict() == raw


def test_nested_obligation_payload_is_detached_from_raw_input() -> None:
    raw = {
        "action": "complete",
        "reason": "ok",
        "obligations": [{"nested": ["before"]}],
    }
    decision = admit_typed_model_decision(
        raw,
        request_contract=ModelRequestContract.REASONING_BOUNDARY_CONTINUATION,
    )
    assert isinstance(decision, ReasoningBoundaryCompleteDecision)

    raw["obligations"][0]["nested"][0] = "after"
    assert decision.obligations[0]["nested"] == ("before",)

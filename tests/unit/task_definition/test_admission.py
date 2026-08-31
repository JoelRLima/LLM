from __future__ import annotations

from agent.llm.admitted_decisions import (
    TaskContractDecision,
    TaskContractNeedsInputDecision,
    TaskSpecDecision,
    admit_typed_model_decision,
)
from agent.llm.decision_contract import (
    ModelRequestContract,
    admit_model_decision_value,
)
from tests.support.task_definition import make_contract, make_spec


def test_authority_decisions_use_existing_exact_admission_boundary() -> None:
    contract = make_contract()
    raw_contract = {"action": "define_contract", "contract": contract.to_dict()}
    admitted = admit_typed_model_decision(
        raw_contract,
        request_contract=ModelRequestContract.TASK_CONTRACT,
        step_type="task_contract",
    )
    assert isinstance(admitted, TaskContractDecision)
    assert admitted.contract == contract

    spec = make_spec(contract)
    raw_spec = {"action": "define_spec", "spec": spec.to_dict()}
    assert isinstance(
        admit_typed_model_decision(
            raw_spec,
            request_contract=ModelRequestContract.TASK_SPEC,
            step_type="task_spec",
        ),
        TaskSpecDecision,
    )


def test_malformed_authority_output_fails_closed() -> None:
    contract = make_contract()
    assert (
        admit_model_decision_value(
            {"action": "define_contract", "contract": contract.to_dict(), "tool": "shell"},
            request_contract=ModelRequestContract.TASK_CONTRACT,
            step_type="task_contract",
        )
        is None
    )
    assert (
        admit_typed_model_decision(
            {"action": "needs_input", "reason": "need context", "question": "Which file?"},
            request_contract=ModelRequestContract.TASK_CONTRACT,
            step_type="task_contract",
        ).__class__
        is TaskContractNeedsInputDecision
    )
    assert (
        admit_model_decision_value(
            {"action": "define_spec", "spec": {"task_id": "task-1"}},
            request_contract=ModelRequestContract.TASK_SPEC,
            step_type="task_spec",
        )
        is None
    )

from __future__ import annotations

import json
from typing import Any

from agent.llm.decision_contract import ModelRequestContract
from agent.task_definition.models import TaskContract, TaskSpec, TaskSpecPhase


def make_contract(
    task_id: str = "task-1",
    objective: str = "Do the thing",
    **overrides: Any,
) -> TaskContract:
    values: dict[str, Any] = {
        "task_id": task_id,
        "objective": objective,
        "summary": "A bounded task",
        "requirements": ("one", "two"),
        "constraints": ("no external side effects",),
        "invariants": ("preserve authority",),
        "success_criteria": ("evidence is available",),
        "out_of_scope": ("unrelated work",),
        "stop_conditions": ("required evidence is unavailable",),
        "assumptions": ("workspace is available",),
        "open_questions": (),
    }
    values.update(overrides)
    return TaskContract(**values)


def make_phase(
    phase_id: str = "phase-1",
    *,
    depends_on: tuple[str, ...] = (),
) -> TaskSpecPhase:
    return TaskSpecPhase(
        phase_id=phase_id,
        title=f"Phase {phase_id}",
        goal=f"Complete {phase_id}",
        requirements=("read the relevant inputs",),
        invariants=("do not bypass the authority binding",),
        acceptance_criteria=("record bounded evidence",),
        evidence_requirements=("test output",),
        depends_on=depends_on,
    )


def make_spec(
    contract: TaskContract,
    phases: tuple[TaskSpecPhase, ...] | None = None,
    **overrides: Any,
) -> TaskSpec:
    values: dict[str, Any] = {
        "task_id": contract.task_id,
        "contract_version": contract.version,
        "contract_digest": contract.digest(),
        "phases": phases or (make_phase(),),
        "architecture": "Descriptive specification above the executable Plan.",
        "global_requirements": ("use the existing planning owner",),
        "global_invariants": ("do not turn phases into executable tool calls",),
        "global_acceptance": ("all required checks pass",),
    }
    values.update(overrides)
    return TaskSpec(**values)


def task_definition_response(gateway: Any, request: Any) -> str | None:
    """Return a valid Contract/Spec response for canonical model requests."""

    request_contract = getattr(request.request_contract, "value", request.request_contract)
    if request_contract == ModelRequestContract.TASK_CONTRACT.value:
        prompt = str(request.messages[-1].content) if request.messages else ""
        marker = "task_id exato: "
        try:
            task_id = prompt.split(marker, 1)[1].splitlines()[0].strip()
            objective = prompt.split("objective exato: ", 1)[1].splitlines()[0]
        except IndexError as exc:
            raise AssertionError("Task Contract prompt did not carry its exact identity") from exc
        contract = make_contract(
            task_id=task_id,
            objective=objective,
            summary="Deterministic task authority fixture",
            requirements=("execute the declared objective",),
            constraints=("use the canonical planner and tool boundary",),
            invariants=("persist Contract and Spec before normal execution",),
            success_criteria=("the normal runtime produces the requested observation",),
            out_of_scope=("unrelated workspace changes",),
            stop_conditions=("task authority cannot be admitted",),
            assumptions=("the isolated workspace is available",),
        )
        gateway._last_task_contract = contract
        return json.dumps(
            {"action": "define_contract", "contract": contract.to_dict()},
            ensure_ascii=False,
        )
    if request_contract == ModelRequestContract.TASK_SPEC.value:
        contract = getattr(gateway, "_last_task_contract", None)
        if not isinstance(contract, TaskContract):
            raise AssertionError("Task Spec requested before Task Contract")
        spec = make_spec(
            contract,
            phases=(
                make_phase(
                    "phase-1",
                ),
            ),
            architecture="Descriptive specification above the executable Plan.",
            global_requirements=("use the existing planning owner",),
            global_invariants=("resolve authority before planner/tool execution",),
            global_acceptance=("normal execution produces canonical evidence",),
        )
        return json.dumps({"action": "define_spec", "spec": spec.to_dict()}, ensure_ascii=False)
    return None

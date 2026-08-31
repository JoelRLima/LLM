"""Deterministic scripted gateway for campaign evaluation."""

from __future__ import annotations

import json
from functools import partial
from pathlib import Path
from typing import Any, Callable, Iterable

from agent.evaluation.scripted_gateway_logic import scripted_plan_response, scripted_response
from agent.evaluation.trace import RecordingGateway
from agent.llm.contracts import ModelRequest, ModelResponse, ProviderCapabilities, StreamEvent
from agent.llm.decision_contract import ModelRequestContract
from agent.task_definition.models import TaskContract, TaskSpec, TaskSpecPhase


class ScriptedEvaluationGateway:
    """Deterministic in-process gateway for contract evaluation."""

    provider_name = "scripted-evaluation"
    model = "scripted-evaluation"
    profile = {"temperature": 0.0, "max_tokens": 512}
    endpoint_identity = "in-process://scripted-evaluation"
    provider_model_id = "scripted-evaluation"
    capabilities = ProviderCapabilities(streaming=False)
    supports_task_definition = True

    def __init__(self, objective: str, *, fixture_marker: str | None = None) -> None:
        self.objective = objective
        self.fixture_marker = str(fixture_marker or "").strip()
        self.dispatch_objective = (
            f"{self.fixture_marker}: {objective}" if self.fixture_marker else objective
        )
        self.calls: list[ModelRequest] = []
        self._last_task_contract: TaskContract | None = None

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.calls.append(request)
        system = str(request.messages[0].content) if request.messages else ""
        prompt = str(request.messages[-1].content) if request.messages else ""
        request_contract = getattr(request.request_contract, "value", request.request_contract)
        if request_contract == ModelRequestContract.TASK_CONTRACT.value:
            content = self._task_contract_response(prompt)
        elif request_contract == ModelRequestContract.TASK_SPEC.value:
            content = self._task_spec_response()
        else:
            content = self._response(system, prompt)
        return ModelResponse(
            content=content,
            provider_metadata={"observed_provider_model_id": self.provider_model_id},
        )

    def stream(self, request: ModelRequest) -> Iterable[StreamEvent]:
        del request
        raise AssertionError("Scripted evaluation must use canonical completion")

    def count_tokens(self, text: str) -> int:
        """Deterministic text-only fallback; never an exact chat count."""

        return max(1, len(text) // 4)

    def _response(self, system: str, prompt: str) -> str:
        return scripted_response(self, system, prompt)

    def _plan_response(self, prompt: str) -> str:
        return scripted_plan_response(self.dispatch_objective, prompt)

    def _task_contract_response(self, prompt: str) -> str:
        marker = "task_id exato: "
        try:
            task_id = prompt.split(marker, 1)[1].splitlines()[0].strip()
        except IndexError as exc:
            raise AssertionError("Task Contract prompt did not carry task_id") from exc
        contract = TaskContract(
            task_id=task_id,
            objective=self.objective,
            summary="Deterministic evaluation task authority",
            requirements=("execute the declared evaluation objective",),
            constraints=("use the canonical AgentApplication planner and tools",),
            invariants=("persist and resolve the Contract and Spec before execution",),
            success_criteria=("the evaluation observation is produced by the normal runtime",),
            out_of_scope=("unrelated workspace changes",),
            stop_conditions=("the canonical task authority cannot be admitted",),
            assumptions=("the isolated evaluation workspace is available",),
        )
        self._last_task_contract = contract
        return json.dumps(
            {"action": "define_contract", "contract": contract.to_dict()},
            ensure_ascii=False,
        )

    def _task_spec_response(self) -> str:
        contract = self._last_task_contract
        if contract is None:
            raise AssertionError("Task Spec requested before Task Contract")
        spec = TaskSpec(
            task_id=contract.task_id,
            contract_version=contract.version,
            contract_digest=contract.digest(),
            phases=(
                TaskSpecPhase(
                    phase_id="evaluation",
                    title="Evaluation execution",
                    goal="Run the objective through the canonical planner and tool boundary",
                    requirements=("preserve the admitted task authority",),
                    invariants=("do not turn descriptive phases into executable plan steps",),
                    acceptance_criteria=("normal evaluation execution completes",),
                    evidence_requirements=("persisted Contract and Spec are available",),
                ),
            ),
            architecture="Descriptive evaluation specification above the executable Plan.",
            global_requirements=("use the existing planning owner",),
            global_invariants=("resolve task authority before planner/tool execution",),
            global_acceptance=("the evaluation result contains canonical runtime evidence",),
        )
        return json.dumps({"action": "define_spec", "spec": spec.to_dict()}, ensure_ascii=False)


def _scripted_factory(
    objective: str,
    _workspace: Path,
    *,
    fixture_marker: str | None = None,
) -> RecordingGateway:
    return RecordingGateway(
        ScriptedEvaluationGateway(objective, fixture_marker=fixture_marker)
    )


_scripted_factory._accepts_fixture_marker = True  # type: ignore[attr-defined]


def bind_fixture_marker(
    factory: Callable[..., Any], marker: str
) -> Callable[..., Any]:
    if getattr(factory, "_accepts_fixture_marker", False) is not True:
        return factory
    return partial(factory, fixture_marker=marker)


__all__ = ["ScriptedEvaluationGateway", "bind_fixture_marker"]

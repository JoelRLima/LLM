"""Behavioral campaign for intent, mutation evidence, and task completion."""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from llm_agent.agent.code.workflows import CodingWorkflowService
from llm_agent.agent.interaction.resolver import InteractionResolver
from llm_agent.agent.interaction.types import InteractionAction, InteractionAmbiguity
from llm_agent.agent.llm.contracts import ModelResponse, ProviderCapabilities
from llm_agent.agent.llm.model_profile import resolve_gateway_model_profile
from llm_agent.agent.planning.task_completion import review_task_completion
from llm_agent.agent.planning.task_completion_constraints import (
    proposal_only_mutation_observed,
    required_validation_satisfied,
)
from llm_agent.agent.planning.task_semantics_authority import admit_effect_authority
from llm_agent.agent.planning.task_semantics_inference import infer_effect_semantics
from llm_agent.agent.runtime.budget import TaskBudgetLedger
from llm_agent.agent.runtime.context import RuntimeLimits, TaskExecutionContext, TaskStatus
from llm_agent.agent.state import AgentState
from llm_agent.agent.task_definition.models import TaskDefinitionRef
from llm_agent.agent.task_definition.repository import TaskDefinitionRepository
from llm_agent.agent.task_definition.resolver import TaskContextResolver
from llm_agent.cancellation import CancellationToken
from llm_agent.workspace.paths import WorkspacePaths
from scripts.run_predictability_campaign import _exit_code
from tests.support.task_definition import make_contract, make_spec
from tests.unit.interaction._helpers import decision, session

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "predictability" / "scenarios.json"


def _task_definition_ref(
    tmp_path: Path,
) -> tuple[TaskDefinitionRef, TaskDefinitionRepository]:
    paths = WorkspacePaths(
        workspace_id="predictability-campaign",
        data_dir=tmp_path / "task-data",
        state_dir=tmp_path / "task-state",
        cache_dir=tmp_path / "task-cache",
    )
    paths.ensure_directories()
    repository = TaskDefinitionRepository(paths)
    contract = make_contract("predictability-repair", "Corrija module.py e valide.")
    repository.save_contract(contract)
    repository.save_spec(make_spec(contract))
    return repository.load_ref(contract.task_id), repository


class _RepairGateway:
    provider_name = "predictability-scripted"
    capabilities = ProviderCapabilities()

    def __init__(self, target: Path, original: str) -> None:
        self.calls: list[Any] = []
        self.target = target
        self.original = original
        self.restored_before_repair = False
        self.responses = [
            json.dumps(
                {"changes": [{"path": "module.py", "kind": "modify", "content": "def value(:\n"}]}
            ),
            json.dumps(
                {
                    "changes": [
                        {
                            "path": "module.py",
                            "kind": "modify",
                            "content": "def value():\n    return 1\n",
                        }
                    ]
                }
            ),
        ]

    def complete(self, request: Any) -> ModelResponse:
        self.calls.append(request)
        if len(self.calls) == 3:
            self.restored_before_repair = (
                self.target.read_text(encoding="utf-8") == self.original
            )
        if request.messages and "independent engineering outcome verifier" in request.messages[0].content:
            body = "\n".join(item.content for item in request.messages)
            evidence = list(dict.fromkeys(re.findall(r'"(?:source_id|evidence_id)"\s*:\s*"([^"]+)"', body)))
            return ModelResponse(
                content=json.dumps(
                    {
                        "verdict": "SUPPORTED",
                        "reason": "scripted evidence review",
                        "evidence_ids": evidence[:8],
                    }
                )
            )
        return ModelResponse(content=self.responses.pop(0))

    def stream(self, _request: Any) -> Any:
        raise NotImplementedError

    def count_tokens(self, text: str) -> int:
        return len(text) // 4


class _ApproveRepair:
    requires_explicit_approval = True

    def approve(self, _preview: Any, _assessment: Any) -> bool:
        return True


def _cases() -> dict[str, list[dict[str, Any]]]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", _cases()["intent_cases"], ids=lambda case: case["id"])
def test_intent_campaign_matches_external_scope_oracles(case: dict[str, Any]) -> None:
    authority = admit_effect_authority(case["objective"])
    candidates = infer_effect_semantics(case["objective"])

    assert list(authority.requested_effects) == case["requested"]
    assert [item.effect for item in candidates.prohibited_intents] == case["prohibited"]
    assert candidates.proposal_only is case.get("proposal_only", False)
    if "candidate_effects" in case:
        assert [item.effect for item in candidates.requested_intents] == case[
            "candidate_effects"
        ]


def test_ambiguous_intent_requests_clarification_with_one_model_call() -> None:
    subject = "Maybe improve this project."
    current_session, gateway = session(
        [
            decision(
                action="clarify",
                ambiguity="effect",
                grounding="current_turn",
                operation_requested=True,
                evidence=subject,
            )
        ]
    )

    outcome = InteractionResolver(current_session).resolve(
        boundary="natural", subject=subject, snapshot=current_session.messages
    )

    assert outcome.decision.action is InteractionAction.CLARIFY
    assert outcome.decision.ambiguity is InteractionAmbiguity.EFFECT
    assert len(gateway.calls) == 1


@pytest.mark.parametrize(("mismatch_count", "expected_exit"), [(0, 0), (1, 1), (12, 1)])
def test_campaign_runner_fails_when_candidate_misses_oracles(
    mismatch_count: int, expected_exit: int
) -> None:
    assert _exit_code({"oracle_mismatch_count": mismatch_count}) == expected_exit


@pytest.mark.parametrize(
    "case", _cases()["validation_cases"], ids=lambda case: case["id"]
)
def test_validation_campaign_classifies_result_histories(case: dict[str, Any]) -> None:
    state = AgentState()
    state.tool_history = [
        {"tool": "code_task", "result": result} for result in case["history"]
    ]
    owner = SimpleNamespace(
        requires_validation=True,
        admitted_effects=("write",),
        proposal_only=case.get("proposal_only", False),
    )
    orchestrator = SimpleNamespace(
        agent_state=state,
        _admitted_intent=owner,
        _cancelled=case.get("cancelled", False),
    )

    assert required_validation_satisfied(orchestrator) is case["expected"]
    assert proposal_only_mutation_observed(orchestrator) is case.get(
        "proposal_violation", False
    )


def test_cancel_blocks_terminal_completion_after_a_validated_real_write(
    tmp_path: Path,
) -> None:
    target = tmp_path / "README.md"
    target.write_text("campaign artifact\n", encoding="utf-8")
    assert target.read_text(encoding="utf-8") == "campaign artifact\n"

    state = AgentState()
    state.record_tool_result(
        "file_writer",
        {"file_path": str(target)},
        {
            "ok": True,
            "status": "succeeded",
            "executed": True,
            "data": {
                "metadata": {
                    "mutation_occurred": True,
                    "applied": True,
                    "final_state": "applied",
                    "affected_resources": [str(target)],
                    "surviving_resources": [str(target)],
                    "validation_status": "passed",
                }
            },
        },
    )
    orchestrator = SimpleNamespace(
        agent_state=state,
        _admitted_intent=SimpleNamespace(
            requires_validation=True,
            admitted_effects=("write",),
        ),
        _cancelled=True,
        _task_failed=False,
        _emit=lambda *_args, **_kwargs: None,
        tool_registry=None,
    )

    assert required_validation_satisfied(orchestrator) is True
    assert review_task_completion(orchestrator).reason_code == "cancelled"
    assert len(state.tool_history) == 1


def test_resume_repair_uses_real_artifact_and_latest_validation(tmp_path: Path) -> None:
    target = tmp_path / "settings.ini"
    target.write_text("broken =\n", encoding="utf-8")
    target.write_text("mode = safe\n", encoding="utf-8")
    assert target.read_text(encoding="utf-8") == "mode = safe\n"

    state = AgentState()
    for validation in (None, "passed"):
        state.record_tool_result(
            "file_writer",
            {"file_path": str(target)},
            {
                "ok": True,
                "status": "succeeded",
                "executed": True,
                "data": {
                    "metadata": {
                        "mutation_occurred": True,
                        "applied": True,
                        "final_state": "applied",
                        "affected_resources": [str(target)],
                        "surviving_resources": [str(target)],
                        "validation_status": validation,
                    }
                },
            },
        )
    orchestrator = SimpleNamespace(
        agent_state=state,
        _admitted_intent=SimpleNamespace(
            requires_validation=True,
            admitted_effects=("write",),
        ),
        _cancelled=False,
    )

    assert required_validation_satisfied(orchestrator) is True
    assert admit_effect_authority("Write settings.ini and validate it.").requested_effects == (
        "write",
    )


def test_code_task_rolls_back_failed_validation_then_repairs_real_file(
    tmp_path: Path,
) -> None:
    target = tmp_path / "module.py"
    original = "def value():\n    return 0\n"
    target.write_text(original, encoding="utf-8")
    gateway = _RepairGateway(target, original)
    context = TaskExecutionContext(
        model_gateway=gateway,
        model_profile=resolve_gateway_model_profile({}, gateway),
        cancellation=CancellationToken(),
        limits=RuntimeLimits(max_output_tokens=512, max_repair_attempts=2),
    )
    service = CodingWorkflowService(tmp_path, context)

    result = service.change(
        "Corrija value",
        ["module.py"],
        repair=True,
        approver=_ApproveRepair(),
    )

    assert result.status is TaskStatus.SUCCEEDED
    assert target.read_text(encoding="utf-8") == "def value():\n    return 1\n"
    assert result.artifacts[0].metadata["validation"] == "passed"
    assert result.artifacts[0].metadata["final_state"] == "applied"
    assert result.artifacts[0].metadata["rollback_occurred"] is False
    assert len(gateway.calls) == 4
    assert gateway.restored_before_repair is True

    state = AgentState()
    state.record_tool_result(
        "code_task",
        {"targets": ["module.py"]},
        {
            "ok": result.ok,
            "status": result.status.value,
            "executed": True,
            "data": {
                "artifacts": [
                    {"metadata": dict(result.artifacts[0].metadata)}
                ]
            },
        },
    )
    reference, repository = _task_definition_ref(tmp_path)
    state.objective = "Corrija module.py e valide."
    state.root_task_id = reference.task_id
    state.task_definition_ref = reference
    checkpoint = state.to_checkpoint_dict()
    restored = AgentState(budget_ledger=TaskBudgetLedger())
    restored.from_checkpoint_dict(checkpoint)
    assert restored.task_definition_ref == reference
    state = restored
    orchestrator = SimpleNamespace(
        agent_state=state,
        _admitted_intent=SimpleNamespace(
            requires_validation=True,
            admitted_effects=("write",),
        ),
        _cancelled=False,
        _task_failed=False,
        _emit=lambda *_args, **_kwargs: None,
        tool_registry=None,
        workspace_root=tmp_path,
        task_context_resolver=TaskContextResolver(repository),
    )
    assert review_task_completion(orchestrator).accepted is True

    target.write_text("def value():\n    return 2\n", encoding="utf-8")
    state.record_tool_result(
        "code_task",
        {"targets": ["module.py"]},
        {
            "ok": True,
            "status": "succeeded",
            "executed": True,
            "data": {
                "metadata": {
                    "mutation_occurred": True,
                    "applied": True,
                    "final_state": "applied",
                    "affected_resources": [str(target)],
                    "surviving_resources": [str(target)],
                }
            },
        },
    )
    rejected = review_task_completion(orchestrator)
    assert rejected.accepted is False
    assert rejected.reason_code == "required_validation_missing"


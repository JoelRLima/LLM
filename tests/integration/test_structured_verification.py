from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from llm_agent.agent.application import AgentApplication
from llm_agent.agent.approval import AutoApprove
from llm_agent.agent.llm.contracts import ModelRequest, ModelResponse
from llm_agent.agent.llm.decision_contract import ModelRequestContract
from llm_agent.agent.task_definition.models import TaskContract
from llm_agent.agent.task_definition.repository import TaskDefinitionRepository
from llm_agent.agent.task_definition.resolver import TaskContextResolver
from tests.integration.test_agent_application_evaluation import JourneyGateway
from tests.integration.test_outcome_planning import _initialized_paths


class VerificationGateway(JourneyGateway):
    def __init__(self, objective: str, kind: str, target: str) -> None:
        super().__init__(objective, "CAP_READ")
        self.kind = kind
        self.target = target

    def complete(self, request: ModelRequest) -> ModelResponse:
        response = super().complete(request)
        contract_name = getattr(request.request_contract, "value", request.request_contract)
        if contract_name != ModelRequestContract.TASK_CONTRACT.value:
            return response
        payload = json.loads(response.content)
        contract = TaskContract.from_dict(payload["contract"])
        contract = TaskContract.from_dict(
            {
                **contract.to_dict(),
                "success_criteria": [
                    *contract.success_criteria,
                    "the declared workspace artifact satisfies its requested shape",
                ],
                "verification_checks": [
                    {
                        "check_id": "artifact_shape",
                        "kind": self.kind,
                        "field": "success_criteria",
                        "index": len(contract.success_criteria),
                        "path": self.target,
                        **(
                            {"required_fields": ["status", "result"]}
                            if self.kind == "json_fields"
                            else {}
                        ),
                    }
                ],
            }
        )
        self._last_task_contract = contract
        return ModelResponse(
            content=json.dumps({"action": "define_contract", "contract": contract.to_dict()}),
            reasoning=response.reasoning,
            usage=response.usage,
            finish_reason=response.finish_reason,
            provider_metadata=dict(response.provider_metadata),
        )


@pytest.mark.parametrize(
    ("kind", "exists", "content", "expected_status", "expected_success", "reason"),
    [
        ("file_exists", True, "private artifact text", "satisfied", True, "file_exists"),
        ("file_exists", False, None, "violated", False, "missing_file"),
        ("json_fields", True, '{"status":"ok","result":42}', "satisfied", True, "required_fields_present"),
        ("json_fields", True, '{"status":"ok"}', "violated", False, "required_fields_missing"),
        ("json_fields", True, "{invalid", "violated", False, "invalid_json"),
    ],
)
def test_first_delivery_structured_checks_run_and_gate_real_application(
    tmp_path: Path,
    kind: str,
    exists: bool,
    content: str | None,
    expected_status: str,
    expected_success: bool,
    reason: str,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "notes.txt").write_text("CAP_READ_EVIDENCE\n", encoding="utf-8")
    target = "artifact.json" if kind == "json_fields" else "artifact.txt"
    if exists:
        (workspace / target).write_text(content or "", encoding="utf-8")
    objective = "CAP_READ: leia notes.txt e informe CAP_READ_EVIDENCE."
    gateway = VerificationGateway(objective, kind, target)
    with AgentApplication.create(
        paths=_initialized_paths(tmp_path),
        workspace=workspace,
        gateway=gateway,
        approval_policy=AutoApprove(),
        configure_logging=False,
    ) as application:
        result = application.run(objective)
        state = application.orchestrator.agent_state
        reference = state.task_definition_ref
        assert reference is not None
        record = TaskDefinitionRepository(application.workspace_paths).resolve(reference)
        materialization = TaskContextResolver().materialize(record.contract, record.spec)
        expected_requirement_id = next(
            item.requirement_id
            for item in materialization.requirement_references
            if item.source == "contract.success_criteria" and item.index == 1
        )

        assert result.success is expected_success
        assert [item["tool"] for item in state.tool_history] == ["file_reader"]
        projection = result.snapshot.projection_facts.plan_coverage
        verification = projection["requirement_verification"]
        check = verification["checks"][0]
        assert check["requirement_id"] == expected_requirement_id
        assert check["status"] == expected_status
        assert check["reason_code"] == reason
        assert verification["definition"]["contract_digest"] == reference.contract_digest
        assert datetime.fromisoformat(check["observed_at"].replace("Z", "+00:00"))
        assert "evidence" not in check and "content" not in check
        encoded_receipt = json.dumps(result.receipt, ensure_ascii=False)
        assert expected_requirement_id in encoded_receipt
        assert reference.contract_digest in encoded_receipt
        assert "private artifact text" not in encoded_receipt

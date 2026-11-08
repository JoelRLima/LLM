from __future__ import annotations

import hashlib
import json
from pathlib import Path

from llm_agent.agent.application import AgentApplication
from llm_agent.agent.approval import AutoApprove
from llm_agent.agent.llm.contracts import ModelRequest, ModelResponse
from llm_agent.agent.llm.decision_contract import ModelRequestContract
from llm_agent.agent.task_definition.models import TaskContract
from tests.integration.test_agent_application_evaluation import JourneyGateway
from tests.integration.test_outcome_planning import _initialized_paths


class RepairGateway(JourneyGateway):
    """Admit a JSON shape check and repair it through the normal tool path."""

    target = "sample.py"

    def __init__(
        self, objective: str, *, repeat: bool = False, read_only: bool = False,
    ) -> None:
        super().__init__(objective, "CAP_READ" if read_only else "CAP_MODIFY")
        self.repeat = repeat
        self.read_only = read_only
        self.engineering_calls = 0
        self.reasoning_calls = 0
        self.reasoning_prompts: list[str] = []

    def complete(self, request: ModelRequest) -> ModelResponse:
        contract_name = getattr(request.request_contract, "value", request.request_contract)
        if contract_name == ModelRequestContract.REASONING_BOUNDARY_CONTINUATION.value:
            self.reasoning_calls += 1
            self.reasoning_prompts.append("\n".join(message.content for message in request.messages))
            if self.repeat:
                content = json.dumps({
                    "action": "execute",
                    "plan": [{"tool": "file_reader", "args": {"file_path": self.target}}],
                })
            elif self.reasoning_calls == 1:
                content = json.dumps({
                    "action": "execute",
                    "plan": [{
                        "tool": "code_task",
                        "args": {
                            "action": "modify",
                            "objective": self.objective,
                            "targets": [self.target],
                        },
                    }],
                })
            else:
                content = json.dumps({"action": "complete", "reason": "requirements satisfied"})
            self.calls.append(request)
            return ModelResponse(content=content)

        prompt = "\n".join(message.content for message in request.messages)
        if "Objetivo de engenharia:" in prompt:
            self.engineering_calls += 1
            content = (
                '{"status":"ok","result":42}'
                if self.engineering_calls > 1
                else '{"status":"ok"}'
            )
            response_content = json.dumps({
                "decision": "CHANGE",
                "rationale": "Complete the explicitly requested JSON fields.",
                "reason_code": "NONE",
                "question": "",
                "changes": [{
                    "path": self.target,
                    "kind": "edit",
                    "edits": [{
                        "operation": "replace",
                        "start_line": 1,
                        "end_line": 1,
                        "content": content,
                    }],
                }],
            })
            self.calls.append(request)
            return ModelResponse(content=response_content)

        response = super().complete(request)
        if contract_name == ModelRequestContract.TASK_CONTRACT.value:
            payload = json.loads(response.content)
            contract = TaskContract.from_dict(payload["contract"])
            contract = TaskContract.from_dict({
                **contract.to_dict(),
                "success_criteria": [
                    *contract.success_criteria,
                    "sample.py contains JSON fields status and result",
                ],
                "verification_checks": [{
                    "check_id": "artifact_shape",
                    "kind": "json_fields",
                    "field": "success_criteria",
                    "index": len(contract.success_criteria),
                    "path": self.target,
                    "required_fields": ["status", "result"],
                }],
            })
            self._last_task_contract = contract
            return ModelResponse(content=json.dumps({
                "action": "define_contract", "contract": contract.to_dict(),
            }))
        return response

    def _plan_response(self) -> str:
        if self.read_only:
            return json.dumps({"plan": [{
                "tool": "file_reader",
                "args": {"file_path": self.target},
            }]})
        return json.dumps({"plan": [{
            "tool": "code_task",
            "args": {
                "action": "modify",
                "objective": self.objective,
                "targets": [self.target],
            },
        }]})


def test_failed_json_requirement_is_repaired_and_reobserved_from_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "sample.py").write_text('{"status":"old"}\n', encoding="utf-8")
    objective = "altere sample.py"
    gateway = RepairGateway(objective)

    with AgentApplication.create(
        paths=_initialized_paths(tmp_path),
        workspace=workspace,
        gateway=gateway,
        approval_policy=AutoApprove(),
        configure_logging=False,
    ) as application:
        result = application.run(objective)

    assert (workspace / "sample.py").exists(), json.dumps({
        "result": result.to_dict(),
        "contracts": [getattr(item.request_contract, "value", item.request_contract) for item in gateway.calls],
    }, default=str)
    final_bytes = (workspace / "sample.py").read_bytes()
    assert result.success is True, json.dumps({
        "reasoning_calls": gateway.reasoning_calls,
        "engineering_calls": gateway.engineering_calls,
        "contracts": [getattr(item.request_contract, "value", item.request_contract) for item in gateway.calls],
        "result": result.to_dict(),
    }, default=str)
    assert json.loads(final_bytes) == {"status": "ok", "result": 42}
    verification = result.snapshot.projection_facts.plan_coverage["requirement_verification"]
    check = verification["checks"][0]
    assert check["status"] == "satisfied"
    assert check["evidence_digest"] == hashlib.sha256(final_bytes).hexdigest()
    assert verification["definition"]["contract_digest"]
    assert check["observed_at"]
    assert gateway.reasoning_calls == 2
    assert "violated" in gateway.reasoning_prompts[0]
    assert "satisfied" in gateway.reasoning_prompts[1]
    # Two writes and the final check are all backed by the actual workspace state.
    assert [item["tool"] for item in result.receipt["tools"]] == ["code_task", "code_task"]


def test_repeated_read_after_failed_requirement_stays_blocked(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "sample.py").write_text('{"status":"old"}\n', encoding="utf-8")
    objective = "altere sample.py"
    gateway = RepairGateway(objective, repeat=True)

    with AgentApplication.create(
        paths=_initialized_paths(tmp_path),
        workspace=workspace,
        gateway=gateway,
        approval_policy=AutoApprove(),
        configure_logging=False,
    ) as application:
        result = application.run(objective)

    verification = result.snapshot.projection_facts.plan_coverage["requirement_verification"]
    assert result.success is False
    assert verification["checks"][0]["status"] in {"violated", "inconclusive"}
    assert gateway.reasoning_calls == 1
    assert json.loads((workspace / "sample.py").read_text(encoding="utf-8")) == {
        "status": "ok",
    }


def test_read_only_objective_cannot_gain_write_authority_from_failed_check(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    original = '{"status":"old"}\n'
    (workspace / "sample.py").write_text(original, encoding="utf-8")
    objective = "leia sample.py"
    gateway = RepairGateway(objective, read_only=True)

    with AgentApplication.create(
        paths=_initialized_paths(tmp_path),
        workspace=workspace,
        gateway=gateway,
        approval_policy=AutoApprove(),
        configure_logging=False,
    ) as application:
        result = application.run(objective)

    assert result.success is False
    assert (workspace / "sample.py").read_text(encoding="utf-8") == original
    assert gateway.reasoning_calls == 1
    assert all(item["tool"] == "file_reader" for item in result.receipt["tools"])

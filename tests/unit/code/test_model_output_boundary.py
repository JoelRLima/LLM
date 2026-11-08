import json

import pytest

from llm_agent.agent.code.context_selection import SelectedFile
from llm_agent.agent.code.outcome_verifier import (
    CodeOutcomeVerdict,
    CodeOutcomeVerifier,
    CodeProposalKind,
    bind_selected_file_evidence,
)
from llm_agent.agent.llm.contracts import ModelResponse
from llm_agent.agent.llm.structured_output import StructuredOutputError
from llm_agent.agent.runtime.context import TaskStatus
from tests.unit.code.test_coding_workflows import FakeGateway, RecordingApprover, _service


class ResponseGateway(FakeGateway):
    def complete(self, request):
        self.calls.append(request)
        return self.responses.pop(0)


@pytest.mark.parametrize("tri_state", [False, True])
@pytest.mark.parametrize(("reason", "calls"), [("length", 2), ("content_filter", 1)])
def test_interrupted_proposal_cannot_reach_approval_or_mutation(tmp_path, tri_state, reason, calls):
    target = tmp_path / "a.py"
    target.write_text("value = 1\n", encoding="utf-8")
    changes = [{"path": "a.py", "kind": "modify", "content": "value = 2\n"}]
    payload = {"changes": changes}
    if tri_state:
        payload.update(decision="CHANGE", rationale="requested change", reason_code="NONE", question="")
    gateway = ResponseGateway([ModelResponse(content=json.dumps(payload), finish_reason=reason)] * 2)
    service = _service(tmp_path, gateway)
    approver = RecordingApprover()
    if tri_state:
        with pytest.raises(StructuredOutputError):
            service.propose_code_decision("Set value to 2", ["a.py"])
    else:
        result = service.change("Set value to 2", ["a.py"], approver=approver)
        assert result.status is TaskStatus.FAILED
    assert target.read_text(encoding="utf-8") == "value = 1\n"
    assert approver.calls == 0
    assert len(gateway.calls) == calls


@pytest.mark.parametrize("reason", ["length", "content_filter"])
def test_interrupted_verdict_cannot_seal_supported_outcome(tmp_path, reason):
    (tmp_path / "a.py").write_bytes(b"value = 1\n")
    selected = SelectedFile("a.py", 100, ("explicit target",), "ignored", "value = 1\n")
    manifest = bind_selected_file_evidence(tmp_path, [selected])
    file_record = next(item for item in manifest.records if item.kind == "FILE_CONTENT")
    service = _service(tmp_path, FakeGateway())
    result = CodeOutcomeVerifier(service.context, tmp_path).verify(
        "Keep a.py correct", CodeProposalKind.NO_CHANGE, manifest, target_paths=("a.py",),
        complete=lambda request: ModelResponse(
            content=json.dumps({"verdict": "SUPPORTED", "reason": "evidence", "evidence_ids": [file_record.evidence_id]}),
            finish_reason=reason,
        ),
    )
    assert result.verdict is CodeOutcomeVerdict.INSUFFICIENT
    assert result.failure_code == "CODE_VERIFICATION_INSUFFICIENT"

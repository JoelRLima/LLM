import hashlib
import json
from types import SimpleNamespace

import pytest

import agent.code.workflow_application as workflow_application
from agent.cancellation import CancellationToken
from agent.code.change_models import ChangeKind, ChangeSet, FileChange
from agent.code.context_selection import SelectedFile
from agent.code.outcome_verifier import (
    CodeEvidenceManifest,
    CodeEvidenceRecord,
    CodeOutcomeSeal,
    CodeOutcomeVerdict,
    CodeOutcomeVerification,
    CodeOutcomeVerifier,
    CodeProposalDecision,
    CodeProposalKind,
    CodeProposalReasonCode,
    bind_selected_file_evidence,
    parse_code_proposal,
)
from agent.code.validation import ValidationStatus
from agent.code.workflow_application import apply_changes
from agent.code.workflows import CodingWorkflowService
from agent.llm.contracts import ModelResponse, ProviderCapabilities
from agent.llm.model_profile import resolve_gateway_model_profile
from agent.planning.completion_observations import refresh_executed_effects
from agent.planning.task_semantics import (
    EffectIntent,
    TaskIntent,
    TaskObligation,
    TaskSemantics,
)
from agent.runtime.context import RuntimeLimits, TaskExecutionContext, TaskStatus
from agent.state import AgentState


def _proposal(decision="CHANGE", rationale="do it", reason_code="NONE", question="", changes=None):
    return {
        "decision": decision,
        "rationale": rationale,
        "reason_code": reason_code,
        "question": question,
        "changes": changes if changes is not None else [],
    }


def test_code_proposal_decision_is_closed_and_never_builds_empty_changeset():
    change = {"path": "a.py", "kind": "modify", "content": "value = 2\n"}
    parsed = parse_code_proposal(_proposal(changes=[change]), "fix a.py")

    assert isinstance(parsed, CodeProposalDecision)
    assert parsed.kind is CodeProposalKind.CHANGE
    assert parsed.changes[0].kind is ChangeKind.MODIFY

    with pytest.raises(Exception, match="INVALID PROPOSAL DECISION"):
        parse_code_proposal({**_proposal(), "metadata": {}}, "fix a.py")
    with pytest.raises(ValueError):
        CodeProposalDecision(
            CodeProposalKind.NO_CHANGE,
            "already correct",
            CodeProposalReasonCode.NONE,
            "",
            (FileChange("a.py", ChangeKind.MODIFY, content="same"),),
        )


def test_runtime_evidence_ids_and_verifier_support_are_not_model_authored(tmp_path):
    source = tmp_path / "a.py"
    source.write_bytes(b"value = 1\n")
    selected = SelectedFile(
        "a.py", 100, ("target explícito",),
        "ignored-by-runtime",
        "value = 1\n",
    )
    manifest = bind_selected_file_evidence(tmp_path, [selected])
    file_record = next(item for item in manifest.records if item.kind == "FILE_CONTENT")
    assert file_record.evidence_id != "ignored-by-runtime"

    context = SimpleNamespace(
        model_profile=SimpleNamespace(model="test"),
        limits=SimpleNamespace(max_output_tokens=128),
        metadata={},
        model_gateway=SimpleNamespace(capabilities=ProviderCapabilities()),
        cancellation=SimpleNamespace(cancelled=False),
    )
    verifier = CodeOutcomeVerifier(context, tmp_path)
    result = verifier.verify(
        "keep a.py correct",
        CodeProposalKind.NO_CHANGE,
        manifest,
        target_paths=("a.py",),
        complete=lambda _request: ModelResponse(
            content=json.dumps(
                {
                    "verdict": "SUPPORTED",
                    "reason": "runtime evidence is complete",
                    "evidence_ids": [file_record.evidence_id],
                }
            )
        ),
    )

    assert result.verdict is CodeOutcomeVerdict.SUPPORTED
    assert result.evidence_ids == (file_record.evidence_id,)


def test_same_content_change_is_rejected_before_approval_commit_or_validation(tmp_path):
    target = tmp_path / "a.py"
    target.write_bytes(b"same\n")
    calls = {"approval": 0, "validation": 0}

    class Policy:
        def assess(self, *_args):
            calls["approval"] += 1
            return SimpleNamespace(confidence=1.0, reasons=(), requires_confirmation=False)

    class Validator:
        def validate(self, *_args, **_kwargs):
            calls["validation"] += 1
            raise AssertionError("no-op must not validate")

    service = SimpleNamespace(
        root=tmp_path,
        approval_policy=Policy(),
        context=SimpleNamespace(cancellation=SimpleNamespace(cancelled=False)),
        validator=Validator(),
    )
    approver = SimpleNamespace(
        requires_explicit_approval=True,
        approve=lambda *_args: (_ for _ in ()).throw(AssertionError("no approval")),
    )
    result = apply_changes(
        service,
        ChangeSet("keep same", (FileChange("a.py", ChangeKind.MODIFY, content="same\n"),)),
        requested_targets=("a.py",),
        approver=approver,
    )

    assert result.status is TaskStatus.FAILED
    assert result.failure_code == "CODE_CHANGE_NOOP"
    assert calls == {"approval": 0, "validation": 0}
    assert target.read_text(encoding="utf-8") == "same\n"


def test_atomic_no_change_uses_independent_verifier_and_returns_runtime_seal(tmp_path):
    target = tmp_path / "a.py"
    target.write_bytes(b"value = 1\n")

    class Gateway:
        provider_name = "fake"
        capabilities = ProviderCapabilities()

        def __init__(self):
            self.calls = []

        def complete(self, request):
            self.calls.append(request)
            if len(self.calls) == 1:
                return ModelResponse(
                    content=json.dumps(_proposal(
                        decision="NO_CHANGE",
                        rationale="the observed implementation already satisfies the objective",
                    ))
                )
            envelope = json.loads(self.calls[-1].messages[1].content)
            file_id = next(
                    record["source_id"]
                for record in envelope["records"]
                if record["source_kind"] == "code_evidence"
                and record["data"]["kind"] == "FILE_CONTENT"
            )
            return ModelResponse(
                content=json.dumps(
                    {
                        "verdict": "SUPPORTED",
                        "reason": "complete current file evidence",
                        "evidence_ids": [file_id],
                    }
                )
            )

        def stream(self, request):
            del request
            raise NotImplementedError

        def count_tokens(self, text):
            return len(text) // 4

    gateway = Gateway()
    context = TaskExecutionContext(
        model_gateway=gateway,
        model_profile=resolve_gateway_model_profile({}, gateway),
        cancellation=CancellationToken(),
        limits=RuntimeLimits(max_output_tokens=512, max_model_calls=4),
    )
    result = CodingWorkflowService(tmp_path, context).change(
        "mantenha a.py correta",
        ("a.py",),
        decision_mode=True,
    )

    assert result.status is TaskStatus.SUCCEEDED
    assert result.artifacts[0].metadata["mutation_occurred"] is False
    assert result.metadata["code_outcome"]["kind"] == "NO_CHANGE"
    assert result.metadata["code_outcome"]["verification"] == "SUPPORTED"
    assert result.metadata["code_outcome"]["cited_evidence_ids"]
    assert len(gateway.calls) == 2


class _CompletionRegistry:
    @staticmethod
    def descriptor(name):
        return SimpleNamespace(
            name=name,
            capabilities=frozenset({"read", "write", "validate"}),
        )


def _nested_no_change_result(manifest, seal, *, top_level=False, occurred=False):
    metadata = {} if top_level else {
        "code_evidence_manifest": manifest.to_dict(),
        "code_outcome": seal.to_dict(),
    }
    result = {
        "ok": True,
        "done": True,
        "status": "succeeded",
        "executed": True,
        "data": {
            "status": "succeeded",
            "metadata": metadata,
        },
        "mutation_attempted": True,
        "mutation_occurred": occurred,
        "persisted_mutation": occurred,
        "surviving_mutation": occurred,
        "affected_files": ["a.py"],
    }
    if top_level:
        result["code_outcome"] = seal.to_dict()
    return result


def _completion_owner(tmp_path, result):
    state = AgentState()
    semantics = TaskSemantics(
        TaskIntent(
            "Mantenha a.py correta.",
            ("write",),
            effect_intents=(EffectIntent("write", "a.py"),),
        ),
        [TaskObligation("effect:write", "effect", "Escrever a.py.", effect="write")],
        _strict_evidence=True,
    )
    state.set_task_semantics(semantics)
    state.objective = "Mantenha a.py correta."
    state.record_tool_result(
        "code_task",
        {"action": "modify", "targets": ["a.py"]},
        result,
    )
    return state, SimpleNamespace(
        agent_state=state,
        root=tmp_path,
        workspace_root=tmp_path,
        tool_registry=_CompletionRegistry(),
        _task_failed=False,
        _cancelled=False,
        _emit=lambda *_args, **_kwargs: None,
    )


def test_completion_owner_binds_only_exact_nested_runtime_seal(tmp_path):
    target = tmp_path / "a.py"
    target.write_bytes(b"value = 1\n")
    selected = SelectedFile("a.py", 1, (), "ignored", "value = 1\n")
    manifest = bind_selected_file_evidence(tmp_path, [selected])
    file_record = next(item for item in manifest.records if item.kind == "FILE_CONTENT")
    seal = CodeOutcomeSeal(
        kind="NO_CHANGE",
        verification="SUPPORTED",
        evidence_manifest_id=manifest.manifest_id,
        evidence_current=True,
        verified_resources=("a.py",),
        cited_evidence_ids=(file_record.evidence_id,),
    )
    state, authority = _completion_owner(
        tmp_path,
        _nested_no_change_result(manifest, seal),
    )

    refresh_executed_effects(authority)

    assert state.waived_effects == ["write"]
    assert state.pending_effects() == ()


def test_completion_owner_rejects_top_level_seal_and_stale_file(tmp_path):
    target = tmp_path / "a.py"
    target.write_bytes(b"value = 1\n")
    selected = SelectedFile("a.py", 1, (), "ignored", "value = 1\n")
    manifest = bind_selected_file_evidence(tmp_path, [selected])
    file_record = next(item for item in manifest.records if item.kind == "FILE_CONTENT")
    seal = CodeOutcomeSeal(
        kind="NO_CHANGE",
        verification="SUPPORTED",
        evidence_manifest_id=manifest.manifest_id,
        evidence_current=True,
        verified_resources=("a.py",),
        cited_evidence_ids=(file_record.evidence_id,),
    )

    top_level_state, top_level_authority = _completion_owner(
        tmp_path,
        _nested_no_change_result(manifest, seal, top_level=True),
    )
    refresh_executed_effects(top_level_authority)
    assert top_level_state.pending_effects() == ("write",)

    stale_state, stale_authority = _completion_owner(
        tmp_path,
        _nested_no_change_result(manifest, seal),
    )
    target.write_bytes(b"value = 2\n")
    refresh_executed_effects(stale_authority)
    assert stale_state.pending_effects() == ("write",)


def test_completion_owner_rejects_no_change_with_mutation_or_unbounded_scope(tmp_path):
    target = tmp_path / "a.py"
    target.write_bytes(b"value = 1\n")
    selected = SelectedFile("a.py", 1, (), "ignored", "value = 1\n")
    manifest = bind_selected_file_evidence(tmp_path, [selected])
    file_record = next(item for item in manifest.records if item.kind == "FILE_CONTENT")
    seal = CodeOutcomeSeal(
        kind="NO_CHANGE",
        verification="SUPPORTED",
        evidence_manifest_id=manifest.manifest_id,
        evidence_current=True,
        verified_resources=("a.py",),
        cited_evidence_ids=(file_record.evidence_id,),
    )
    state, authority = _completion_owner(
        tmp_path,
        _nested_no_change_result(manifest, seal, occurred=True),
    )
    refresh_executed_effects(authority)
    assert state.waived_effects == []

    broad_semantics = TaskSemantics(
        TaskIntent("Mantenha o workspace correto.", ("write",)),
        [TaskObligation("effect:write", "effect", "Escrever.", effect="write")],
        _strict_evidence=True,
    )
    broad_state = AgentState()
    broad_state.set_task_semantics(broad_semantics)
    broad_state.record_tool_result(
        "code_task",
        {"action": "modify", "targets": ["a.py"]},
        _nested_no_change_result(manifest, seal),
    )
    broad_authority = SimpleNamespace(
        root=tmp_path,
        workspace_root=tmp_path,
        tool_registry=_CompletionRegistry(),
        agent_state=broad_state,
    )
    refresh_executed_effects(broad_authority)
    assert broad_state.pending_effects() == ("write",)


def test_low_risk_change_skips_selective_verifier(tmp_path, monkeypatch):
    target = tmp_path / "a.py"
    target.write_bytes(b"value = 1\n")
    calls = []

    class ExplodingVerifier:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("small low-risk CHANGE must skip verifier")

    class Policy:
        def assess(self, *_args):
            calls.append("assessment")
            return SimpleNamespace(
                confidence=1.0,
                reasons=(),
                requires_confirmation=False,
            )

    class Validator:
        def validate(self, *_args, **_kwargs):
            calls.append("validation")
            return SimpleNamespace(status=ValidationStatus.PASSED, diagnostics=())

    monkeypatch.setattr(workflow_application, "CodeOutcomeVerifier", ExplodingVerifier)
    service = SimpleNamespace(
        root=tmp_path,
        approval_policy=Policy(),
        context=SimpleNamespace(
            cancellation=SimpleNamespace(cancelled=False),
            metadata={},
        ),
        validator=Validator(),
        _diagnostic_dict=lambda item: item,
    )
    result = apply_changes(
        service,
        ChangeSet(
            "change a.py",
            (FileChange("a.py", ChangeKind.MODIFY, content="value = 2\n"),),
        ),
        requested_targets=("a.py",),
    )

    assert result.status is TaskStatus.SUCCEEDED
    assert calls == ["assessment", "validation"]
    assert target.read_bytes() == b"value = 2\n"


def test_delete_change_uses_prepared_operation_evidence_before_commit(tmp_path, monkeypatch):
    target = tmp_path / "a.py"
    target.write_bytes(b"value = 1\n")
    calls = []
    manifest = bind_selected_file_evidence(
        tmp_path,
        [SelectedFile("a.py", 1, (), "ignored", "value = 1\n")],
    )

    class Policy:
        def assess(self, *_args):
            calls.append("assessment")
            return SimpleNamespace(
                confidence=1.0,
                reasons=(),
                requires_confirmation=False,
            )

    class Validator:
        def validate(self, *_args, **_kwargs):
            calls.append("validation")
            return SimpleNamespace(status=ValidationStatus.PASSED, diagnostics=())

    class Verifier:
        def __init__(self, *_args, **_kwargs):
            pass

        def verify(self, *_args, prepared_records=(), **_kwargs):
            calls.append("verifier")
            assert prepared_records
            prepared_id = prepared_records[0].source_id
            return CodeOutcomeVerification(
                CodeOutcomeVerdict.SUPPORTED,
                "prepared delete evidence",
                (prepared_id,),
            )

    class Approver:
        requires_explicit_approval = True

        def approve(self, *_args):
            calls.append("approval")
            return True

    monkeypatch.setattr(workflow_application, "CodeOutcomeVerifier", Verifier)
    service = SimpleNamespace(
        root=tmp_path,
        approval_policy=Policy(),
        context=SimpleNamespace(
            cancellation=SimpleNamespace(cancelled=False),
            metadata={},
        ),
        validator=Validator(),
        _diagnostic_dict=lambda item: item,
    )
    result = apply_changes(
        service,
        ChangeSet(
            "delete a.py",
            (
                FileChange(
                    "a.py",
                    ChangeKind.DELETE,
                    base_hash=hashlib.sha256(b"value = 1\n").hexdigest(),
                ),
            ),
        ),
        requested_targets=("a.py",),
        approver=Approver(),
        evidence_manifest=manifest,
    )

    assert result.status is TaskStatus.SUCCEEDED
    assert calls == ["assessment", "verifier", "approval", "validation"]
    assert not target.exists()


def test_target_choice_requires_two_grounded_targets():
    first = CodeEvidenceRecord(
        "file-a",
        "FILE_CONTENT",
        path="a.py",
        sha256="a" * 64,
        content="a\n",
    )
    second = CodeEvidenceRecord(
        "file-b",
        "FILE_CONTENT",
        path="b.py",
        sha256="b" * 64,
        content="b\n",
    )
    literal = CodeEvidenceRecord(
        "user",
        "USER_LITERAL",
        content="choose one",
        sha256="c" * 64,
    )
    manifest = CodeEvidenceManifest(
        "manifest",
        (first, second, literal),
        total_text_chars=14,
    )

    supported = CodeOutcomeVerifier._project_verdict(
        {
            "verdict": "SUPPORTED",
            "reason": "two grounded candidates",
            "evidence_ids": ["file-a", "file-b", "user"],
        },
        manifest,
        CodeProposalKind.NEEDS_INPUT,
        target_paths=("a.py", "b.py"),
        reason_code=CodeProposalReasonCode.TARGET_CHOICE_REQUIRED,
        additional_evidence_ids=(),
    )
    insufficient = CodeOutcomeVerifier._project_verdict(
        {
            "verdict": "SUPPORTED",
            "reason": "only one grounded candidate",
            "evidence_ids": ["file-a", "user"],
        },
        manifest,
        CodeProposalKind.NEEDS_INPUT,
        target_paths=("a.py", "b.py"),
        reason_code=CodeProposalReasonCode.TARGET_CHOICE_REQUIRED,
        additional_evidence_ids=(),
    )

    assert supported.verdict is CodeOutcomeVerdict.SUPPORTED
    assert insufficient.verdict is CodeOutcomeVerdict.INSUFFICIENT

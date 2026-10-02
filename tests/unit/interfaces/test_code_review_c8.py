"""Review projection fidelity, approval presentation and broker identity sentinels."""

from __future__ import annotations

import hashlib
from dataclasses import FrozenInstanceError, fields, replace
from types import SimpleNamespace
from typing import Any, get_args, get_type_hints

import pytest

from llm_agent.agent.code.changes import ChangePreview
from llm_agent.agent.code.policy import ProposalAssessment
from llm_agent.agent.code.workflow_application_support import _approval_authority, _preview_sha256
from llm_agent.application import code_commands, code_review
from llm_agent.application.agent_boundary import ApprovalDecision, ApprovalRequest
from llm_agent.application.code_review import CodeReviewAssessment, CodeReviewPreview, _project_code_review
from llm_agent.interfaces.cli import ui
from llm_agent.interfaces.cli.attention import ApprovalBroker


def test_public_types_are_owned_frozen_and_finite() -> None:
    assert code_review.__all__ == ["CodeReviewPreview", "CodeReviewAssessment"]
    for dto, owner, names, value in (
        (CodeReviewPreview, ChangePreview, ["change_set_id", "affected_files", "diff"], CodeReviewPreview("id", (), "")),
        (CodeReviewAssessment, ProposalAssessment, ["confidence", "reasons"], CodeReviewAssessment(0.8)),
    ):
        assert dto is not owner and not issubclass(dto, owner)
        assert dto.__module__ == "llm_agent.application.code_review"
        assert [field.name for field in fields(dto)] == names
        with pytest.raises(FrozenInstanceError):
            setattr(value, names[0], None)


@pytest.mark.parametrize(
    "diff",
    ["", "--- a/\u00e1.py\r\n+++ b/\u00e1.py\n+\U0001f600\n", "\u00e7\U0001f600\r\n" * 7000],
    ids=["empty", "unicode", "large_unicode"],
)
def test_projection_copies_all_values_without_processing(diff: str) -> None:
    preview = ChangePreview("id-\u00e7", ("z.py", "a/\u00e7.py", "z.py"), diff, True)
    assessment = ProposalAssessment(0.8123456789, True, ("z", "\u00e7", "z"))
    projected, assessed = _project_code_review(preview, assessment)
    assert type(projected) is CodeReviewPreview and type(assessed) is CodeReviewAssessment
    assert projected.change_set_id == preview.change_set_id
    assert projected.affected_files == preview.affected_files
    assert type(projected.affected_files) is tuple
    assert projected.diff == diff
    assert assessed.confidence == assessment.confidence
    assert assessed.confidence.hex() == assessment.confidence.hex()
    assert assessed.reasons == assessment.reasons and type(assessed.reasons) is tuple
    assert not hasattr(projected, "mutation_occurred")
    assert not hasattr(assessed, "requires_confirmation")
    assert preview.mutation_occurred is True and assessment.requires_confirmation is True


def test_public_factory_signature_resolves_to_application_dtos() -> None:
    factory = get_type_hints(code_commands.execute_code_command)["approval_factory"]
    parameters, result = get_args(factory)
    assert parameters == [bool]
    callback = next(arg for arg in get_args(result) if arg is not type(None))
    assert get_args(callback) == ([CodeReviewPreview, CodeReviewAssessment], bool)


def test_agent_decision_precedes_projection_and_retains_mutation_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    preview = ChangePreview("id", ("a.py",), "diff", True)
    assessment = ProposalAssessment(0.9, False)
    assert _preview_sha256(preview) != _preview_sha256(replace(preview, mutation_occurred=False))
    events: list[str] = []
    original = code_commands._project_code_review

    def project(p: ChangePreview, a: ProposalAssessment) -> tuple[CodeReviewPreview, CodeReviewAssessment]:
        assert p is preview and a is assessment
        events.append("project")
        return original(p, a)

    def callback(p: CodeReviewPreview, a: CodeReviewAssessment) -> bool:
        assert type(p) is CodeReviewPreview and type(a) is CodeReviewAssessment
        assert not hasattr(a, "requires_confirmation")
        events.append("callback")
        return True

    monkeypatch.setattr(code_commands, "_project_code_review", project)
    adapter = code_commands._CallbackApprover(callback)
    assert adapter.requires_explicit_approval is True
    assert _approval_authority(preview, assessment, None).mode == "autonomous"
    assert not events
    assert _approval_authority(preview, replace(assessment, requires_confirmation=True), None).mode == "approval_pending"
    assert not events
    authority = _approval_authority(preview, assessment, adapter)
    assert events == ["project", "callback"]
    assert authority.mode == "explicit_approved" and authority.explicit is True
    assert authority.preview_sha256 == _preview_sha256(preview)
    # The Agent alone chooses whether a non-explicit approver is needed.
    owner_approver = SimpleNamespace(approve=lambda *_: pytest.fail("unexpected approval"))
    assert _approval_authority(preview, assessment, owner_approver).mode == "approval_not_required"


@pytest.mark.parametrize("answer, expected", [("sim", True), ("yes", True), ("", False), ("n\u00e3o", False)])
@pytest.mark.parametrize("diff", ["", "--- a.py\r\n+\u00e7\U0001f600\n"])
def test_console_render_lock_prompt_and_decision_parity(
    monkeypatch: pytest.MonkeyPatch, answer: str, expected: bool, diff: str,
) -> None:
    output: list[Any] = []
    approver = ui.ConsoleChangeApprover()

    def print_value(value: Any, **kwargs: Any) -> None:
        assert approver._lock.locked()
        output.append((value, kwargs))

    def prompt(message: str) -> str:
        assert approver._lock.locked()
        assert message == "[bold cyan]Aplicar este ChangeSet? [s/sim/y/yes = sim; Enter/n/nao/n\u00e3o/no = n\u00e3o]:[/bold cyan] "
        return answer

    monkeypatch.setattr(ui.console, "print", print_value)
    approver.prompt = prompt
    assert approver.approve(CodeReviewPreview("id", (), diff), CodeReviewAssessment(.8123456789, ("z", "\u00e7", "z"))) is expected
    assert not approver._lock.locked()
    assert output[0][0] == "[bold yellow]Proposta exige confirma\u00e7\u00e3o (confian\u00e7a 81%).[/bold yellow]"
    assert output[1:4] == [("- z", {"markup": False}), ("- \u00e7", {"markup": False}), ("- z", {"markup": False})]
    assert output[4][0].code == (diff or "(diff vazio)")
    assert output[4][0].word_wrap is True


def test_console_yes_returns_before_lock_prompt_or_field_access(monkeypatch: pytest.MonkeyPatch) -> None:
    class Unreadable:
        def __getattribute__(self, _name: str) -> Any:
            pytest.fail("--yes accessed review fields")

    approver = ui.ConsoleChangeApprover(True, prompt=lambda _: pytest.fail("prompted"))
    monkeypatch.setattr(ui.console, "print", lambda *_: pytest.fail("rendered"))
    approver._lock = None  # type: ignore[assignment]
    assert approver.approve(Unreadable(), Unreadable()) is True  # type: ignore[arg-type]


def test_broker_request_and_fingerprint_match_pre_c8_sentinel(monkeypatch: pytest.MonkeyPatch) -> None:
    diff = "\u00e7\U0001f600\r\n" * 7000
    paths = ("dir/\u00e1.py",) * 2 + tuple(f"{i}.py" for i in range(18))
    reasons = ("\u00e7",) * 2 + tuple(f"reason {i}" for i in range(18))
    preview, assessment = _project_code_review(
        ChangePreview("id-\u00e7", paths, diff, True), ProposalAssessment(.8123456789, True, reasons),
    )
    captured: list[ApprovalRequest] = []
    broker = ApprovalBroker()

    def request(value: ApprovalRequest) -> ApprovalDecision:
        captured.append(value)
        return ApprovalDecision.APPROVED

    monkeypatch.setattr(broker, "request", request)
    assert broker.approve_change(preview, assessment) is True
    actual = captured[0]
    assert actual.action == "code_changeset"
    assert actual.resource == ", ".join(paths[:16])
    assert actual.prompt == "Aplicar ChangeSet id-\u00e7? arquivos=20; confian\u00e7a=81%"
    assert dict(actual.metadata) == {
        "change_set_id": "id-\u00e7", "affected_files": paths[:16], "confidence": .8123456789,
        "reasons": reasons[:16], "proposed_diff": diff[:24_000], "proposed_diff_truncated": True,
        "proposed_diff_sha256": hashlib.sha256(diff.encode("utf-8")).hexdigest(),
    }
    assert actual.metadata["proposed_diff_sha256"] == "029cd699eb53f777e2733acffab5c72e5b5e69260aca1a241ceddb34287ac6de"
    # Captured from the unchanged pre-C8 broker body with the original Agent values.
    assert broker.fingerprint(actual) == "d1ee52a3b72cf4594b5b83c15a8963d090ce1530886e0b9afcc1c8eee4b19cf0"


@pytest.mark.parametrize("size", [0, 24_000, 24_001])
def test_broker_diff_bound_is_strict_and_empty_resource_fallback_survives(
    monkeypatch: pytest.MonkeyPatch, size: int,
) -> None:
    captured: list[ApprovalRequest] = []
    broker = ApprovalBroker()
    monkeypatch.setattr(broker, "request", lambda value: captured.append(value) or ApprovalDecision.REJECTED)
    diff = "\u00e7" * size
    assert broker.approve_change(CodeReviewPreview("id", (), diff), CodeReviewAssessment(.5)) is False
    request = captured[0]
    assert request.resource == "workspace"
    assert request.metadata["proposed_diff"] == diff[:24_000]
    assert request.metadata["proposed_diff_truncated"] is (size > 24_000)
    assert request.metadata["proposed_diff_sha256"] == hashlib.sha256(diff.encode("utf-8")).hexdigest()

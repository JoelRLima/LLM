from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.code.change_models import ChangeKind, ChangeSet, FileChange
from agent.code.change_transaction import ChangeSetTransaction
from agent.code.workflow_application_flow_support import run_apply_changes
from agent.interaction.admission import AdmissionContext
from agent.interaction.errors import InteractionAdmissionError
from agent.interaction.intent_claim import (
    ConstraintClaim,
    EffectClaim,
    EvidenceSpan,
    IntentClaimV1,
    TargetSelectorClaim,
)
from agent.interaction.semantic_admission import admit_semantic_candidate
from agent.interaction.types import (
    ActionGrounding,
    InteractionAction,
    InteractionAmbiguity,
    InteractionBoundary,
    InteractionModelDecision,
)
from agent.planning.intent_admission import (
    AuthorityEnvelope,
    IntentAdmissionError,
    admit_intent_claim,
)
from agent.planning.semantic_completion import task_semantics_from_admitted_intent
from agent.planning.target_grounding import GroundingError, ground_intent_claim
from agent.planning.task_completion import required_validation_satisfied, review_task_completion
from agent.resources.contracts import ResourceAccess, ResourceMode, ResourceProvenance
from agent.runtime.context import TaskStatus


def _claim(
    subject: str,
    selectors: tuple[tuple[str, str, str, str, str | None], ...],
    effects: tuple[tuple[str, tuple[str, ...], str], ...] = (),
    constraints: tuple[str, ...] = (),
    *,
    operation: str = "do",
) -> IntentClaimV1:
    spans: list[EvidenceSpan] = []
    selector_claims: list[TargetSelectorClaim] = []
    for selector_id, kind, value, role, evidence_text in selectors:
        text = evidence_text or value
        start = subject.index(text)
        span_id = f"e:{selector_id}"
        spans.append(EvidenceSpan(span_id, start, start + len(text), text))
        selector_claims.append(
            TargetSelectorClaim(selector_id, kind, value, role, (span_id,))
        )
    effect_claims = tuple(
        EffectClaim(effect, polarity, selector_ids, tuple(f"e:{item}" for item in selector_ids))
        for effect, selector_ids, polarity in effects
    )
    constraint_claims = tuple(
        ConstraintClaim(kind, (spans[0].span_id,)) for kind in constraints
    )
    return IntentClaimV1(
        operation,
        "none",
        effect_claims,
        tuple(selector_claims),
        constraint_claims,
        tuple(spans),
    )


def _envelope(
    root: Path | None = None,
    *,
    read: tuple[str, ...] = ("src",),
    write: tuple[str, ...] = ("src",),
    permissions: tuple[str, ...] = ("read", "write", "validate"),
    effects: tuple[str, ...] = ("write",),
) -> AuthorityEnvelope:
    return AuthorityEnvelope(
        parent_permissions=frozenset(permissions),
        granted_effects=frozenset(effects),
        read_resources=tuple(
            ResourceAccess(item, ResourceMode.READ, ResourceProvenance.TRUSTED_DERIVED)
            for item in read
        ),
        write_resources=tuple(
            ResourceAccess(item, ResourceMode.WRITE, ResourceProvenance.TRUSTED_DERIVED)
            for item in write
        ),
        workspace_root=str(root) if root is not None else None,
    )


def _mutation_result(validation: str | None = None) -> dict[str, object]:
    metadata: dict[str, object] = {
        "affected_files": ("src/settings.py",),
        "mutation_occurred": True,
        "applied": True,
        "final_state": "applied",
    }
    if validation is not None:
        metadata["validation"] = validation
    return {
        "ok": True,
        "status": "succeeded",
        "data": {"artifacts": [{"metadata": metadata}]},
    }


def test_preserve_is_not_admitted_when_no_deterministic_owner_exists() -> None:
    claim = _claim(
        "change TIMEOUT",
        (("s1", "symbol", "TIMEOUT", "mutation_target", None),),
        (("write", ("s1",), "requested"),),
        ("preserve",),
    )

    with pytest.raises(IntentAdmissionError, match="INTENT_CONSTRAINT_UNSUPPORTED"):
        admit_intent_claim(
            claim,
            _envelope(),
            current_subject="change TIMEOUT",
        )


def test_require_validation_is_propagated_and_capability_is_independent() -> None:
    claim = _claim(
        "change TIMEOUT",
        (("s1", "symbol", "TIMEOUT", "mutation_target", None),),
        (("write", ("s1",), "requested"),),
        ("require_validation",),
    )
    admitted = admit_intent_claim(claim, _envelope(), current_subject="change TIMEOUT")

    assert admitted.requires_validation is True
    with pytest.raises(IntentAdmissionError, match="INTENT_VALIDATION_CAPABILITY_DENIED"):
        admit_intent_claim(
            claim,
            _envelope(permissions=("read", "write")),
            current_subject="change TIMEOUT",
        )


def test_require_validation_blocks_completion_when_validation_was_skipped(monkeypatch) -> None:
    import agent.planning.task_completion as completion

    state = SimpleNamespace(
        tool_history=[{"result": _mutation_result()}],
        last_result=None,
        terminal_disposition=None,
        pending_effects=lambda: (),
        pending_obligations=lambda: (),
        blocked_obligations=lambda: (),
        prohibited_effects_occurred=lambda: (),
        unrequested_effects=lambda: (),
        terminal_evidence_complete=lambda: True,
    )
    orchestrator = SimpleNamespace(
        agent_state=state,
        _admitted_intent=SimpleNamespace(
            requires_validation=True,
            admitted_effects=("write",),
            proposal_only=False,
        ),
        _task_failed=False,
        _cancelled=False,
    )
    monkeypatch.setattr(completion, "refresh_executed_effects", lambda _owner: None)
    monkeypatch.setattr(completion, "terminal_failure", lambda *_args, **_kwargs: False)

    assert required_validation_satisfied(orchestrator) is False
    review = review_task_completion(orchestrator)
    assert review.accepted is False
    assert review.reason_code == "required_validation_missing"

    state.tool_history = [{"result": _mutation_result("passed")}]
    assert required_validation_satisfied(orchestrator) is True
    assert review_task_completion(orchestrator).accepted is True


def test_proposal_only_owner_blocks_commit_even_when_approver_would_approve(tmp_path: Path) -> None:
    target = tmp_path / "module.py"
    target.write_text("value = 1\n", encoding="utf-8")
    change_set = ChangeSet(
        objective="change module",
        changes=(FileChange("module.py", ChangeKind.MODIFY, content="value = 2\n"),),
    )
    approval_calls: list[bool] = []
    service = SimpleNamespace(
        root=tmp_path,
        context=SimpleNamespace(
            metadata={
                "admitted_intent": SimpleNamespace(
                    mutation_targets=("module.py",),
                    proposal_only=True,
                )
            }
        ),
        approval_policy=SimpleNamespace(
            assess=lambda *_args: SimpleNamespace(
                confidence=1.0,
                reasons=(),
                requires_confirmation=False,
            )
        ),
    )
    approver = SimpleNamespace(
        requires_explicit_approval=False,
        approve=lambda *_args: approval_calls.append(True) or True,
    )

    result = run_apply_changes(
        service,
        change_set,
        approver=approver,
        transaction_factory=ChangeSetTransaction,
        outcome_verifier_factory=object(),
        prepared_change_evidence_factory=object(),
        validate_model_code_task=object(),
        requires_selective_verification=lambda *_args: False,
    )

    assert result.status is TaskStatus.BLOCKED
    assert result.failure_code == "PROPOSAL_ONLY_MUTATION_FORBIDDEN"
    assert approval_calls == []
    assert target.read_text(encoding="utf-8") == "value = 1\n"


def test_semantic_proposal_constraint_is_narrower_than_outer_normal_route() -> None:
    claim = _claim(
        "change TIMEOUT",
        (("s1", "symbol", "TIMEOUT", "mutation_target", None),),
        (("write", ("s1",), "requested"),),
        ("proposal_only",),
    )
    context = AdmissionContext(
        boundary=InteractionBoundary.NATURAL,
        visible_user_text="change TIMEOUT",
        subject="change TIMEOUT",
        authority_envelope=_envelope(),
    )
    decision = InteractionModelDecision(
        action=InteractionAction.RUN,
        directive="do",
        ambiguity=InteractionAmbiguity.NONE,
        grounding=ActionGrounding.CURRENT_TURN,
        operation_requested=True,
        proposal_only=False,
        resume_requested=False,
        evidence="TIMEOUT",
    )

    resolution = admit_semantic_candidate(context, decision, claim)
    assert resolution.admitted_intent is not None
    assert resolution.admitted_intent.proposal_only is True


def test_outer_proposal_flag_cannot_widen_normal_semantic_claim() -> None:
    claim = _claim(
        "change TIMEOUT",
        (("s1", "symbol", "TIMEOUT", "mutation_target", None),),
        (("write", ("s1",), "requested"),),
    )
    context = AdmissionContext(
        boundary=InteractionBoundary.NATURAL,
        visible_user_text="change TIMEOUT",
        subject="change TIMEOUT",
        authority_envelope=_envelope(),
    )
    decision = InteractionModelDecision(
        action=InteractionAction.RUN,
        directive="do",
        ambiguity=InteractionAmbiguity.NONE,
        grounding=ActionGrounding.CURRENT_TURN,
        operation_requested=True,
        proposal_only=True,
        resume_requested=False,
        evidence="TIMEOUT",
    )

    with pytest.raises(InteractionAdmissionError, match="INTERACTION_INTENT_AMBIGUOUS"):
        admit_semantic_candidate(context, decision, claim)


@pytest.mark.parametrize("role", ["source", "topic"])
def test_non_mutating_selector_inside_write_scope_never_becomes_mutation_target(
    tmp_path: Path, role: str
) -> None:
    source_dir = tmp_path / "src"
    source_dir.mkdir()
    (source_dir / "settings.py").write_text("TIMEOUT = 10\n", encoding="utf-8")
    (source_dir / "helpers.py").write_text("HELPER = 1\n", encoding="utf-8")
    subject = "change src/settings.py using src/helpers.py"
    claim = _claim(
        subject,
        (
            ("s1", "path_literal", "src/settings.py", "mutation_target", None),
            ("s2", "path_literal", "src/helpers.py", role, None),
        ),
        (("write", ("s1",), "requested"),),
    )

    _admitted, grounded = ground_intent_claim(
        claim,
        _envelope(tmp_path),
        current_subject=subject,
        workspace_root=tmp_path,
    )

    assert grounded.mutation_targets == ("src/settings.py",)
    assert grounded.for_selector("s2").mutation_authorized is False


def test_read_selector_is_grounded_with_read_authority_but_not_write_intent(tmp_path: Path) -> None:
    source = tmp_path / "src" / "helpers.py"
    source.parent.mkdir()
    source.write_text("HELPER = 1\n", encoding="utf-8")
    subject = "read src/helpers.py"
    claim = _claim(
        subject,
        (("s1", "path_literal", "src/helpers.py", "source", None),),
        operation="read",
    )

    _admitted, grounded = ground_intent_claim(
        claim,
        _envelope(tmp_path, read=("src",), write=("src/helpers.py",)),
        current_subject=subject,
        workspace_root=tmp_path,
    )

    assert grounded.resources == ("src/helpers.py",)
    assert grounded.mutation_targets == ()


def test_memory_resource_is_mutating_only_when_bound_to_memory_write() -> None:
    claim = _claim(
        "write memory",
        (("s1", "resource", "memory", "mutation_target", None),),
        (("write", ("s1",), "requested"),),
    )

    with pytest.raises(IntentAdmissionError, match="INTENT_MEMORY_TARGET_INVALID"):
        admit_intent_claim(
            claim,
            _envelope(read=("memory",), write=("memory",)),
            current_subject="write memory",
        )


def test_literal_grounding_denies_outside_read_scope_before_read_bytes(
    tmp_path: Path, monkeypatch
) -> None:
    subject = "read src/secret.py"
    claim = _claim(
        subject,
        (("s1", "path_literal", "src/secret.py", "source", None),),
        operation="read",
    )
    calls: list[Path] = []

    def spy(path: Path) -> bytes:
        calls.append(path)
        raise AssertionError("read_bytes must not run outside read scope")

    monkeypatch.setattr(Path, "read_bytes", spy)
    with pytest.raises(GroundingError, match="GROUNDING_READ_SCOPE_DENIED"):
        ground_intent_claim(
            claim,
            _envelope(tmp_path, read=("src/allowed",), write=("src",)),
            current_subject=subject,
            workspace_root=tmp_path,
        )
    assert calls == []


def test_resource_kind_cannot_launder_model_selected_filesystem_path() -> None:
    subject = "change the default timeout"
    claim = _claim(
        subject,
        (("s1", "resource", "src/settings.py", "mutation_target", "default timeout"),),
        (("write", ("s1",), "requested"),),
    )

    with pytest.raises(IntentAdmissionError, match="INTENT_LITERAL_TARGET_NOT_EVIDENCED"):
        admit_intent_claim(claim, _envelope(), current_subject=subject)


def test_path_literal_requires_exact_current_user_evidence_and_grounding_is_concrete(
    tmp_path: Path,
) -> None:
    path = tmp_path / "src" / "settings.py"
    path.parent.mkdir()
    path.write_text("TIMEOUT = 10\n", encoding="utf-8")
    subject = "edit src/settings.py"
    claim = _claim(
        subject,
        (("s1", "path_literal", "src/settings.py", "mutation_target", None),),
        (("write", ("s1",), "requested"),),
    )

    admitted, grounded = ground_intent_claim(
        claim,
        _envelope(tmp_path),
        current_subject=subject,
        workspace_root=tmp_path,
    )
    assert admitted.mutation_targets == ("src/settings.py",)
    assert grounded.mutation_targets == ("src/settings.py",)


def test_semantic_completion_projection_keeps_constraint_flags(tmp_path: Path) -> None:
    subject = "change src/settings.py"
    claim = _claim(
        subject,
        (("s1", "path_literal", "src/settings.py", "mutation_target", None),),
        (("write", ("s1",), "requested"),),
        ("proposal_only", "require_validation"),
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "settings.py").write_text("TIMEOUT = 10\n", encoding="utf-8")
    admitted, grounded = ground_intent_claim(
        claim,
        _envelope(tmp_path),
        current_subject=subject,
        workspace_root=tmp_path,
    )

    semantics = task_semantics_from_admitted_intent(subject, admitted, grounded)
    assert semantics.proposal_only is True
    assert semantics.requires_validation is True

"""W14 intent admission, grounding, and resume authority."""

from __future__ import annotations

from typing import Any

from agent.planning.intent_admission import AdmittedIntent, AuthorityEnvelope
from agent.planning.target_grounding import GroundedTargetSet
from agent.planning.task_completion import mark_terminal_blocked
from agent.runtime.task_directives import TaskRunDirective


def _claim_fingerprint(claim: Any) -> str:
    from agent.planning.intent_continuation import claim_fingerprint

    return claim_fingerprint(claim)


def _emit_semantic_audit(orchestrator: Any, event_type: str, data: dict[str, Any]) -> None:
    emit = getattr(orchestrator, "_emit", None)
    if callable(emit):
        try:
            emit(event_type, data)
        except (RuntimeError, TypeError, ValueError):
            return


def _refresh_w14_context_metadata(orchestrator: Any) -> None:
    context = getattr(orchestrator, "_task_execution_context", None)
    metadata = getattr(context, "metadata", None)
    if not isinstance(metadata, dict):
        return
    from agent.runtime.task_execution_context import _authority_metadata

    metadata.update(_authority_metadata(orchestrator))


def _admit_runtime_intent(orchestrator: Any, directive: TaskRunDirective | None) -> str | None:
    """Admit and ground the one semantic claim before planning side effects."""

    orchestrator._admitted_intent = None
    orchestrator._grounded_targets = None
    orchestrator._authority_envelope = None
    claim = getattr(directive, "intent_claim", None)
    if claim is None or not isinstance(directive, TaskRunDirective):
        return None

    from agent.planning.intent_admission import (
        IntentAdmissionError,
        admit_intent_claim,
        authority_envelope_from_orchestrator,
    )
    from agent.planning.intent_continuation import W14IntentContinuation
    from agent.planning.semantic_completion import install_admitted_intent_semantics
    from agent.planning.target_grounding import GroundingError, ground_admitted_targets

    envelope = authority_envelope_from_orchestrator(orchestrator)
    try:
        admitted = admit_intent_claim(claim, envelope, current_subject=directive.subject)
    except (IntentAdmissionError, ValueError) as exc:
        reason_code = str(
            getattr(exc, "reason_code", None)
            or getattr(exc, "code", None)
            or "INTENT_AUTHORITY_DENIED"
        )
        _emit_semantic_audit(
            orchestrator,
            "semantic_intent_admission_denied",
            {
                "contract": "semantic-intent-v1",
                "admitted": False,
                "reason_code": reason_code,
                "evidence_span_ids": [
                    item.span_id for item in getattr(claim, "evidence_spans", ())
                ],
                "claim_fingerprint": _claim_fingerprint(claim),
            },
        )
        return str(
            mark_terminal_blocked(
                orchestrator,
                reason_code=reason_code,
                message=(
                    "A intenção semântica não pôde ser admitida com segurança "
                    f"({reason_code}); nenhuma alteração foi iniciada."
                ),
                status="block",
            )
        )
    try:
        grounded = ground_admitted_targets(admitted, envelope, orchestrator.workspace_root)
        continuation = W14IntentContinuation.from_claim(
            claim,
            subject=directive.subject,
            admitted_intent=admitted,
        )
    except (GroundingError, ValueError) as exc:
        reason_code = str(
            getattr(exc, "reason_code", None)
            or getattr(exc, "code", None)
            or "INTENT_AUTHORITY_DENIED"
        )
        _emit_semantic_audit(
            orchestrator,
            "semantic_intent_admission_denied",
            {
                "contract": "semantic-intent-v1",
                "admitted": False,
                "reason_code": reason_code,
                "evidence_span_ids": [
                    item.span_id for item in getattr(claim, "evidence_spans", ())
                ],
                "claim_fingerprint": _claim_fingerprint(claim),
            },
        )
        return str(
            mark_terminal_blocked(
                orchestrator,
                reason_code=reason_code,
                message=(
                    "A intenção semântica não pôde ser admitida com segurança "
                    f"({reason_code}); nenhuma alteração foi iniciada."
                ),
                status="block",
            )
        )
    orchestrator._authority_envelope = envelope
    orchestrator._admitted_intent = admitted
    orchestrator._grounded_targets = grounded
    install_admitted_intent_semantics(orchestrator, directive.subject, admitted, grounded)
    orchestrator.agent_state.w14_semantic_task = True
    orchestrator.agent_state.w14_intent_continuation = continuation
    _refresh_w14_context_metadata(orchestrator)
    _emit_semantic_audit(
        orchestrator,
        "semantic_grounding",
        {
            "selectors": [
                {
                    "selector_id": item.selector_id,
                    "selector_kind": item.selector_kind,
                    "resource": item.resource,
                    "provenance": item.provenance,
                    "freshness": item.freshness_token,
                    "mutation_bound": item.mutation_authorized,
                }
                for item in grounded.targets
            ],
        },
    )
    _emit_semantic_audit(
        orchestrator,
        "semantic_intent_admitted",
        {
            "operation": admitted.operation,
            "admitted_effects": list(admitted.admitted_effects),
            "grounded_targets": list(grounded.mutation_targets),
            "authority_identity": admitted.authority_identity,
            "claim_fingerprint": admitted.claim_fingerprint,
            "evidence_span_ids": [item.span_id for item in admitted.evidence_spans],
            "constraint_kinds": [item.kind for item in admitted.constraints],
        },
    )
    return None


def _restore_w14_runtime_intent(orchestrator: Any) -> str | None:
    """Rebuild W14 projections from intent-only continuation data."""

    state = orchestrator.agent_state
    if getattr(state, "w14_semantic_task", False) is not True:
        return None
    from agent.planning.intent_admission import (
        IntentAdmissionError,
        admit_intent_claim,
        authority_envelope_from_orchestrator,
    )
    from agent.planning.intent_continuation import W14IntentContinuation
    from agent.planning.semantic_completion import install_admitted_intent_semantics
    from agent.planning.target_grounding import GroundingError, ground_admitted_targets

    projection = getattr(state, "w14_intent_continuation", None)
    directive = getattr(state, "task_run_directive", None)
    if not isinstance(projection, W14IntentContinuation) or not isinstance(directive, TaskRunDirective):
        return str(
            mark_terminal_blocked(
                orchestrator,
                reason_code="W14_CONTINUATION_MISSING",
                message="O checkpoint W14 nao possui continuacao semantica tipada; nenhuma alteracao foi iniciada.",
                status="block",
            )
        )
    if directive.subject != projection.subject:
        return str(
            mark_terminal_blocked(
                orchestrator,
                reason_code="W14_CONTINUATION_SUBJECT_MISMATCH",
                message="A continuacao W14 nao corresponde ao sujeito original; nenhuma alteracao foi iniciada.",
                status="block",
            )
        )
    try:
        claim = projection.claim_object()
        envelope = authority_envelope_from_orchestrator(orchestrator)
        admitted = admit_intent_claim(claim, envelope, current_subject=projection.subject)
        grounded = ground_admitted_targets(admitted, envelope, orchestrator.workspace_root)
        if (
            admitted.proposal_only != projection.proposal_only
            or admitted.requires_validation != projection.requires_validation
        ):
            raise IntentAdmissionError("W14_CONTINUATION_CONSTRAINT_MISMATCH")
    except (IntentAdmissionError, GroundingError, ValueError) as exc:
        reason_code = str(
            getattr(exc, "reason_code", None)
            or getattr(exc, "code", None)
            or "W14_CONTINUATION_AUTHORITY_DENIED"
        )
        return str(
            mark_terminal_blocked(
                orchestrator,
                reason_code=reason_code,
                message="A autoridade W14 atual nao cobre mais a continuacao; nenhuma alteracao foi iniciada.",
                status="block",
            )
        )
    orchestrator._authority_envelope = envelope
    orchestrator._admitted_intent = admitted
    orchestrator._grounded_targets = grounded
    install_admitted_intent_semantics(orchestrator, projection.subject, admitted, grounded)
    state.w14_semantic_task = True
    state.w14_intent_continuation = projection
    _refresh_w14_context_metadata(orchestrator)
    _emit_semantic_audit(
        orchestrator,
        "authority_revalidated",
        {
            "resumed": True,
            "authority_identity": envelope.authority_identity,
            "claim_fingerprint": admitted.claim_fingerprint,
            "grounded_targets": list(grounded.mutation_targets),
        },
    )
    _emit_semantic_audit(
        orchestrator,
        "semantic_grounding",
        {
            "resumed": True,
            "selectors": [
                {
                    "selector_id": item.selector_id,
                    "selector_kind": item.selector_kind,
                    "resource": item.resource,
                    "provenance": item.provenance,
                    "freshness": item.freshness_token,
                    "mutation_bound": item.mutation_authorized,
                }
                for item in grounded.targets
            ],
        },
    )
    return None


def _ensure_w14_runtime_intent(orchestrator: Any) -> str | None:
    """Keep a marked W14 task out of legacy planning when restoration is incomplete."""

    state = getattr(orchestrator, "agent_state", None)
    if getattr(state, "w14_semantic_task", False) is not True:
        return None
    if all(
        isinstance(value, expected)
        for value, expected in (
            (getattr(orchestrator, "_authority_envelope", None), AuthorityEnvelope),
            (getattr(orchestrator, "_admitted_intent", None), AdmittedIntent),
            (getattr(orchestrator, "_grounded_targets", None), GroundedTargetSet),
        )
    ):
        return None
    return str(
        mark_terminal_blocked(
            orchestrator,
            reason_code="MUTATION_W14_AUTHORITY_MISSING",
            message=(
                "A tarefa W14 nao possui autoridade semantica tipada restaurada; "
                "nenhuma alteracao foi iniciada."
            ),
            status="block",
        )
    )


__all__ = [
    "_admit_runtime_intent",
    "_ensure_w14_runtime_intent",
    "_restore_w14_runtime_intent",
]

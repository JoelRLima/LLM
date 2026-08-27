"""Pure matching, projection, and conflict helpers for effect authority."""

from __future__ import annotations

from typing import Any

from agent.planning.task_semantics_positive_proof import PositiveAuthorityProof
from agent.planning.task_semantics_types import EffectIntent, EffectSemantics
from agent.resources.contracts import resources_overlap


def positive_admission_failure(
    candidate: EffectIntent,
    candidates: EffectSemantics,
    proof: PositiveAuthorityProof | None,
) -> str | None:
    if candidate.source.casefold() != "objective":
        return "effect source is not the user objective"
    if candidates.proposal_only:
        return "proposal-only objective cannot create durable authority"
    if proof is None:
        return "no complete structured positive authority proof"
    if proof.effect != candidate.effect or proof.target != candidate.target:
        return "positive authority proof does not bind this effect target"
    return None


def matching_proof(
    candidate: EffectIntent,
    proofs: list[PositiveAuthorityProof],
) -> PositiveAuthorityProof | None:
    exact_branch = next(
        (
            proof
            for proof in proofs
            if proof.effect == candidate.effect
            and proof.target == candidate.target
            and proof.predicate_id == candidate.predicate_id
            and proof.predicate_expected == candidate.predicate_expected
        ),
        None,
    )
    if exact_branch is not None or candidate.predicate_id is not None:
        return exact_branch
    return next(
        (
            proof
            for proof in proofs
            if proof.effect == candidate.effect
            and proof.target == candidate.target
            and proof.predicate_id is None
        ),
        None,
    )


def candidate_projection(proof: PositiveAuthorityProof) -> EffectIntent:
    return EffectIntent(
        proof.effect,
        proof.target,
        condition=proof.condition,
        predicate_id=proof.predicate_id,
        predicate_expected=proof.predicate_expected,
        candidate_role=proof.target_role,
        positive_syntax=False,
    )


def reject_conflicts(decisions: tuple[Any, ...]) -> tuple[Any, ...]:
    """Apply deny dominance and reject overlapping ambiguous branches."""

    from agent.planning.task_semantics_authority import (
        AuthorityDecision,
        EffectAuthorityDecision,
    )

    result = list(decisions)
    for index, decision in enumerate(decisions):
        if not decision.authorized:
            continue
        candidate = decision.candidate
        conflict = next(
            (
                other.candidate
                for other in decisions
                if other.candidate.polarity == "prohibited"
                and _same_effect_scope(candidate, other.candidate)
                and not _complementary_branches(candidate, other.candidate)
            ),
            None,
        )
        if conflict is not None:
            result[index] = EffectAuthorityDecision(
                candidate,
                AuthorityDecision.NOT_AUTHORIZED,
                "overlapping prohibited scope dominates the positive candidate",
            )
            continue
        duplicate_scope = next(
            (
                other.candidate
                for other in decisions[:index]
                if other.authorized
                and _same_effect_scope(candidate, other.candidate)
                and not _same_candidate_branch(candidate, other.candidate)
            ),
            None,
        )
        if duplicate_scope is not None:
            result[index] = EffectAuthorityDecision(
                candidate,
                AuthorityDecision.NOT_AUTHORIZED,
                "overlapping positive branches are ambiguous",
            )
    return tuple(result)


def _same_effect_scope(left: EffectIntent, right: EffectIntent) -> bool:
    return left.effect == right.effect and resources_overlap(left.target, right.target)


def _same_candidate_branch(left: EffectIntent, right: EffectIntent) -> bool:
    return (
        left.predicate_id == right.predicate_id
        and left.predicate_expected == right.predicate_expected
        and left.condition == right.condition
    )


def _complementary_branches(left: EffectIntent, right: EffectIntent) -> bool:
    return (
        left.predicate_id is not None
        and left.predicate_id == right.predicate_id
        and type(left.predicate_expected) is bool
        and type(right.predicate_expected) is bool
        and left.predicate_expected is not right.predicate_expected
    )


__all__ = [
    "candidate_projection",
    "matching_proof",
    "positive_admission_failure",
    "reject_conflicts",
]

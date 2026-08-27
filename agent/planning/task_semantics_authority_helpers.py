"""Pure matching, projection, and conflict helpers for effect authority."""

from __future__ import annotations

from typing import Any

from agent.planning.task_semantics_positive_proof import (
    AuthorityConstraint,
    PositiveAuthorityProof,
)
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


def constraint_projection(constraint: AuthorityConstraint) -> EffectIntent:
    """Expose one canonical constraint through the legacy intent view."""

    candidate_role = (
        constraint.target_role
        if constraint.target_role in {"DESTINATION", "MUTATION_TARGET", "MEMORY"}
        else "UNKNOWN"
    )
    return EffectIntent(
        constraint.effect,
        constraint.target,
        polarity="prohibited",
        condition=constraint.condition,
        predicate_id=constraint.predicate_id,
        predicate_expected=constraint.predicate_expected,
        candidate_role=candidate_role,
        positive_syntax=False,
    )


def apply_constraint_dominance(
    decisions: tuple[Any, ...],
    constraints: tuple[AuthorityConstraint, ...],
) -> tuple[Any, ...]:
    """Apply canonical deny dominance to proof-backed admitted effects."""

    from agent.planning.task_semantics_authority import (
        AuthorityDecision,
        EffectAuthorityDecision,
    )

    result = list(decisions)
    for index, decision in enumerate(decisions):
        if not decision.authorized:
            continue
        admitted = decision.admitted_effect
        if admitted is None:
            continue
        conflict = next(
            (
                constraint
                for constraint in constraints
                if _same_authority_scope(admitted, constraint)
                and not _complementary_authority_branches(admitted, constraint)
            ),
            None,
        )
        if conflict is not None:
            result[index] = EffectAuthorityDecision(
                decision.candidate,
                AuthorityDecision.NOT_AUTHORIZED,
                "canonical constraint scope dominates the positive proof",
            )
            continue
    return tuple(result)


def _same_authority_scope(admitted: Any, constraint: AuthorityConstraint) -> bool:
    return admitted.effect == constraint.effect and resources_overlap(
        admitted.target, constraint.target
    )


def _complementary_authority_branches(
    admitted: Any, constraint: AuthorityConstraint
) -> bool:
    return (
        admitted.predicate_id is not None
        and admitted.predicate_id == constraint.predicate_id
        and type(admitted.predicate_expected) is bool
        and type(constraint.predicate_expected) is bool
        and admitted.predicate_expected is not constraint.predicate_expected
    )


__all__ = [
    "apply_constraint_dominance",
    "candidate_projection",
    "constraint_projection",
    "matching_proof",
    "positive_admission_failure",
]

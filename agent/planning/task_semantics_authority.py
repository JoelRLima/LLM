"""Canonical proof-backed admission for durable objective effects."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from agent.planning.task_semantics_authority_helpers import (
    apply_constraint_dominance,
    candidate_projection,
    constraint_projection,
    matching_proof,
    positive_admission_failure,
)
from agent.planning.task_semantics_positive_proof import (
    AuthorityConstraint,
    AuthorizedEffect,
    PositiveAuthorityProof,
    authorized_effect_from_proof,
    objective_authority_fingerprint,
    parse_objective_authority,
)
from agent.planning.task_semantics_types import EffectIntent, EffectSemantics, TaskSemanticsError


class AuthorityDecision(str, Enum):
    """Closed vocabulary for an authority admission result."""

    AUTHORIZED = "AUTHORIZED"
    NOT_AUTHORIZED = "NOT_AUTHORIZED"
    REQUIRES_CONFIRMATION = "REQUIRES_CONFIRMATION"


_AUTHORITY_FACTORY_TOKEN = object()


@dataclass(frozen=True, slots=True)
class EffectAuthorityDecision:
    """One immutable decision over one advisory effect candidate."""

    candidate: EffectIntent
    decision: AuthorityDecision
    reason: str
    proof: PositiveAuthorityProof | None = None
    admitted_effect: AuthorizedEffect | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.candidate, EffectIntent):
            raise TypeError("candidato de efeito invalido")
        if not isinstance(self.decision, AuthorityDecision):
            raise TypeError("decisao de autoridade invalida")
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise ValueError("decisao de autoridade requer motivo")
        if self.decision is AuthorityDecision.AUTHORIZED:
            self._validate_authorized_fields()
        elif self.admitted_effect is not None:
            raise TypeError("non-authorized decision cannot carry admitted effect")

    def _validate_authorized_fields(self) -> None:
        if not isinstance(self.proof, PositiveAuthorityProof):
            raise TypeError("authorized decision requires proof from the admission owner")
        if not isinstance(self.admitted_effect, AuthorizedEffect):
            raise TypeError("authorized decision requires admitted effect from the admission owner")
        if self.admitted_effect.proof is not self.proof:
            raise TypeError("admitted effect is not sealed to the decision proof")

    @property
    def authorized(self) -> bool:
        return (
            self.decision is AuthorityDecision.AUTHORIZED
            and self.proof is not None
            and self.admitted_effect is not None
        )


@dataclass(frozen=True, slots=True)
class EffectAuthority:
    """Canonical admitted view and the complete candidate decision ledger."""

    objective: str
    decisions: tuple[EffectAuthorityDecision, ...] = ()
    proposal_only: bool = False
    constraints: tuple[AuthorityConstraint, ...] = ()
    _factory_token: object = field(default=None, repr=False, compare=False, kw_only=True)

    def __post_init__(self) -> None:
        if not isinstance(self.objective, str):
            raise TypeError("objetivo da autoridade invalido")
        if any(not isinstance(item, EffectAuthorityDecision) for item in self.decisions):
            raise TypeError("decisoes de autoridade invalidas")
        if any(not isinstance(item, AuthorityConstraint) for item in self.constraints):
            raise TypeError("restricoes de autoridade invalidas")
        if type(self.proposal_only) is not bool:
            raise TypeError("flag de proposta da autoridade invalida")
        if self._factory_token is not _AUTHORITY_FACTORY_TOKEN:
            raise TypeError("autoridade deve ser criada pelo admission owner")
        expected_identity = objective_authority_fingerprint(self.objective)
        if any(
            item.authorized
            and (item.proof is None or item.proof.objective_fingerprint != expected_identity)
            for item in self.decisions
        ):
            raise TypeError("positive authority proof does not belong to this objective")
        if any(
            item.objective_fingerprint != expected_identity for item in self.constraints
        ):
            raise TypeError("authority constraint does not belong to this objective")

    @property
    def authorized_decisions(self) -> tuple[EffectAuthorityDecision, ...]:
        return tuple(item for item in self.decisions if item.authorized)

    @property
    def authorized_effects(self) -> tuple[AuthorizedEffect, ...]:
        return tuple(
            item.admitted_effect
            for item in self.authorized_decisions
            if item.admitted_effect is not None
        )

    @property
    def authorized_intents(self) -> tuple[AuthorizedEffect, ...]:
        return self.authorized_effects

    @property
    def authorized_candidates(self) -> tuple[EffectIntent, ...]:
        return tuple(item.candidate for item in self.authorized_decisions)

    @property
    def positive_authority_proofs(self) -> tuple[PositiveAuthorityProof, ...]:
        return tuple(item.proof for item in self.authorized_decisions if item.proof is not None)

    @property
    def constraint_intents(self) -> tuple[EffectIntent, ...]:
        return tuple(constraint_projection(item) for item in self.constraints)

    @property
    def requested_effects(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(item.effect for item in self.authorized_effects))

    @property
    def admitted_intents(self) -> tuple[EffectIntent, ...]:
        authorized = tuple(item.candidate for item in self.decisions if item.authorized)
        return (*authorized, *self.constraint_intents)

    @property
    def has_conditional_candidate(self) -> bool:
        return any(
            item.candidate.conditional or item.candidate.condition is not None
            for item in self.decisions
        ) or any(item.conditional for item in self.constraints)


def admit_effect_authority(
    objective: str,
    candidates: EffectSemantics | None = None,
) -> EffectAuthority:
    """Admit only positive, bounded, objective-authored effect candidates."""

    if not isinstance(objective, str):
        raise TypeError("objetivo deve ser textual")
    if candidates is not None and not isinstance(candidates, EffectSemantics):
        raise TypeError("candidatos de efeito invalidos")
    from agent.planning.task_semantics_inference import infer_effect_semantics

    if candidates is None:
        candidates = infer_effect_semantics(objective)
    grammar = parse_objective_authority(objective)
    proofs = () if candidates.proposal_only else grammar.positive_proofs
    constraints = grammar.constraints
    unmatched_proofs = list(proofs)
    initial: list[EffectAuthorityDecision] = []
    for candidate in candidates.intents:
        if candidate.polarity == "prohibited":
            initial.append(
                EffectAuthorityDecision(
                    candidate,
                    AuthorityDecision.NOT_AUTHORIZED,
                    "advisory constraint is not a canonical authority fact",
                )
            )
            continue
        proof = matching_proof(candidate, unmatched_proofs)
        if proof is not None:
            unmatched_proofs.remove(proof)
        reason = positive_admission_failure(candidate, candidates, proof)
        admitted = (
            authorized_effect_from_proof(proof, objective=objective)
            if reason is None and proof is not None
            else None
        )
        initial.append(
            EffectAuthorityDecision(
                candidate,
                AuthorityDecision.NOT_AUTHORIZED if reason else AuthorityDecision.AUTHORIZED,
                reason or "complete positive grammar proof",
                proof if admitted is not None else None,
                admitted,
            )
        )
    for proof in unmatched_proofs:
        candidate = candidate_projection(proof)
        admitted = authorized_effect_from_proof(proof, objective=objective)
        initial.append(
            EffectAuthorityDecision(
                candidate,
                AuthorityDecision.AUTHORIZED,
                "complete positive grammar proof",
                proof,
                admitted,
            )
        )
    return EffectAuthority(
        objective,
        apply_constraint_dominance(tuple(initial), constraints),
        proposal_only=candidates.proposal_only,
        constraints=constraints,
        _factory_token=_AUTHORITY_FACTORY_TOKEN,
    )


def authority_for_objective(
    objective: str, authority: EffectAuthority | None
) -> EffectAuthority:
    if authority is None:
        return admit_effect_authority(objective)
    if not isinstance(authority, EffectAuthority) or authority.objective != objective:
        raise TaskSemanticsError("autoridade de efeito nao corresponde ao objetivo")
    return authority


__all__ = [
    "AuthorityDecision",
    "AuthorizedEffect",
    "EffectAuthority",
    "EffectAuthorityDecision",
    "PositiveAuthorityProof",
    "authority_for_objective",
    "admit_effect_authority",
]

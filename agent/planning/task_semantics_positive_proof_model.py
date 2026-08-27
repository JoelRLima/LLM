"""Canonical proof and admitted-effect representations."""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass, field
from typing import Final

from agent.planning.task_semantics_types import PredicateResolutionState, TaskSemanticsError
from agent.resources.contracts import WORKSPACE_RESOURCE, normalize_resource_id

_PROOF_FACTORY_TOKEN: Final[object] = object()
_PROOF_SEAL_KEY: Final[bytes] = hashlib.sha256(
    repr(_PROOF_FACTORY_TOKEN).encode("utf-8")
).digest()


@dataclass(frozen=True, slots=True)
class PositiveAuthorityProof:
    """Immutable evidence that a complete positive production matched."""

    effect: str
    target: str
    authority_source: str
    production_id: str
    governing_clause: str
    governing_span: tuple[int, int]
    consumed_spans: tuple[tuple[int, int], ...]
    consumed_tokens: tuple[str, ...]
    target_role: str
    objective_fingerprint: str
    predicate_id: str | None = None
    predicate_expected: bool | None = None
    condition: str | None = None
    approval_reference: str | None = None
    full_scope_consumed: bool = True
    unresolved_authority_material: tuple[str, ...] = ()
    _factory_token: object = field(default=None, repr=False, compare=False, kw_only=True)
    _integrity_seal: str | None = field(default=None, repr=False, compare=False, kw_only=True)

    def __post_init__(self) -> None:
        self._validate_factory()
        self._validate_effect_target()
        self._validate_identity_and_scope()
        self._validate_predicate()
        self._validate_approval()
        self._seal_or_verify()

    def _validate_factory(self) -> None:
        if self._factory_token is not _PROOF_FACTORY_TOKEN:
            raise TypeError("positive authority proof must be created by the canonical proof owner")

    def _validate_effect_target(self) -> None:
        if self.effect not in {"write", "memory_write"}:
            raise TaskSemanticsError("unsupported positive authority effect")
        if normalize_resource_id(self.target) == WORKSPACE_RESOURCE:
            raise TaskSemanticsError("positive authority proof requires an exact target")

    def _validate_identity_and_scope(self) -> None:
        if self.authority_source != "objective_positive_grammar":
            raise TaskSemanticsError("positive authority source is not canonical")
        if not self.production_id or not self.governing_clause:
            raise TaskSemanticsError("positive authority proof is incomplete")
        start, end = self.governing_span
        if start < 0 or end <= start or not self.consumed_spans:
            raise TaskSemanticsError("positive authority span is invalid")
        if not self.full_scope_consumed or self.unresolved_authority_material:
            raise TaskSemanticsError("positive authority scope is not fully consumed")

    def _validate_predicate(self) -> None:
        if self.predicate_id is None:
            if self.predicate_expected is not None or self.condition is not None:
                raise TaskSemanticsError("positive authority predicate is incomplete")
            return
        if type(self.predicate_expected) is not bool or self.condition is None:
            raise TaskSemanticsError("positive authority predicate is incomplete")

    def _validate_approval(self) -> None:
        if self.approval_reference is not None:
            raise TaskSemanticsError("objective grammar cannot manufacture approval authority")

    def _seal_or_verify(self) -> None:
        expected_seal = _proof_integrity_seal(self)
        if self._integrity_seal is None:
            object.__setattr__(self, "_integrity_seal", expected_seal)
        elif not hmac.compare_digest(self._integrity_seal, expected_seal):
            raise TypeError("positive authority proof integrity check failed")

    @property
    def objective_identity(self) -> str:
        return self.objective_fingerprint

    @property
    def scope_complete(self) -> bool:
        return self.full_scope_consumed

    @property
    def rule_id(self) -> str:
        return self.production_id


@dataclass(frozen=True, slots=True)
class AuthorizedEffect:
    """Operational durable effect sealed to one inspectable positive proof."""

    proof: PositiveAuthorityProof
    _factory_token: object = field(default=None, repr=False, compare=False, kw_only=True)
    _proof_seal: str | None = field(default=None, repr=False, compare=False, kw_only=True)

    def __post_init__(self) -> None:
        if self._factory_token is not _PROOF_FACTORY_TOKEN:
            raise TypeError("authorized effect must be created by the canonical proof owner")
        if not isinstance(self.proof, PositiveAuthorityProof):
            raise TypeError("authorized effect requires a positive authority proof")
        proof_seal = self.proof._integrity_seal
        if proof_seal is None:
            raise TypeError("authorized effect requires an integrity-sealed proof")
        if self._proof_seal is None:
            object.__setattr__(self, "_proof_seal", proof_seal)
        elif not hmac.compare_digest(self._proof_seal, proof_seal):
            raise TypeError("authorized effect is not sealed to its proof")

    @property
    def effect(self) -> str:
        return self.proof.effect

    @property
    def target(self) -> str:
        return self.proof.target

    @property
    def source(self) -> str:
        return "objective"

    @property
    def polarity(self) -> str:
        return "requested"

    @property
    def prohibited(self) -> bool:
        return False

    @property
    def condition(self) -> str | None:
        return self.proof.condition

    @property
    def predicate_id(self) -> str | None:
        return self.proof.predicate_id

    @property
    def predicate_identity(self) -> str | None:
        return self.proof.predicate_id

    @property
    def predicate_expected(self) -> bool | None:
        return self.proof.predicate_expected

    @property
    def expected_predicate_value(self) -> bool | None:
        return self.proof.predicate_expected

    @property
    def predicate_state(self) -> PredicateResolutionState:
        return PredicateResolutionState.UNRESOLVED

    @property
    def predicate_resolution(self) -> PredicateResolutionState:
        return PredicateResolutionState.UNRESOLVED

    @property
    def conditional(self) -> bool:
        return self.proof.predicate_id is not None

    @property
    def candidate_role(self) -> str:
        return self.proof.target_role

    @property
    def objective_fingerprint(self) -> str:
        return self.proof.objective_fingerprint


@dataclass(frozen=True, slots=True)
class _Lexeme:
    value: str
    raw: str
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class _Predicate:
    predicate_id: str
    expected: bool
    condition: str
    target: str | None


@dataclass(frozen=True, slots=True)
class _ProofSpec:
    effect: str
    target: str
    production_id: str
    target_role: str
    predicate: _Predicate | None = None


def _proof_integrity_seal(proof: PositiveAuthorityProof) -> str:
    material = repr(
        (
            proof.effect,
            proof.target,
            proof.authority_source,
            proof.production_id,
            proof.governing_clause,
            proof.governing_span,
            proof.consumed_spans,
            proof.consumed_tokens,
            proof.target_role,
            proof.objective_fingerprint,
            proof.predicate_id,
            proof.predicate_expected,
            proof.condition,
            proof.approval_reference,
            proof.full_scope_consumed,
            proof.unresolved_authority_material,
        )
    ).encode("utf-8")
    return hmac.new(_PROOF_SEAL_KEY, material, hashlib.sha256).hexdigest()


__all__ = ["AuthorizedEffect", "PositiveAuthorityProof"]

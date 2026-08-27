"""Canonical negative constraints and the unified authority grammar result."""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass, field
from typing import Final

from agent.planning.task_semantics_positive_proof_model import (
    _PROOF_FACTORY_TOKEN,
    _PROOF_SEAL_KEY,
    PositiveAuthorityProof,
)
from agent.planning.task_semantics_types import TaskSemanticsError
from agent.resources.contracts import WORKSPACE_RESOURCE, normalize_resource_id

_AUTHORITY_RESULT_FACTORY_TOKEN: Final[object] = object()


@dataclass(frozen=True, slots=True)
class AuthorityConstraint:
    """Immutable canonical negative authority fact from the grammar owner."""

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
    constraint_kind: str = "deny"
    predicate_id: str | None = None
    predicate_expected: bool | None = None
    condition: str | None = None
    full_scope_consumed: bool = True
    unresolved_authority_material: tuple[str, ...] = ()
    _factory_token: object = field(default=None, repr=False, compare=False, kw_only=True)
    _integrity_seal: str | None = field(default=None, repr=False, compare=False, kw_only=True)

    def __post_init__(self) -> None:
        self._validate_factory()
        self._validate_effect_target()
        self._validate_identity_and_scope()
        self._validate_predicate()
        self._seal_or_verify()

    def _validate_factory(self) -> None:
        if self._factory_token is not _PROOF_FACTORY_TOKEN:
            raise TypeError("authority constraint must be created by the canonical grammar owner")

    def _validate_effect_target(self) -> None:
        if self.effect not in {"write", "memory_write"}:
            raise TaskSemanticsError("unsupported authority constraint effect")
        target = normalize_resource_id(self.target)
        object.__setattr__(self, "target", target)
        if self.effect == "memory_write":
            if target != "memory":
                raise TaskSemanticsError("memory authority constraint requires memory scope")
            if self.target_role != "MEMORY":
                raise TaskSemanticsError("memory authority constraint role is invalid")
        elif target == WORKSPACE_RESOURCE:
            if self.target_role != "WORKSPACE":
                raise TaskSemanticsError("global write constraint role is invalid")
        elif self.target_role not in {"DESTINATION", "MUTATION_TARGET"}:
            raise TaskSemanticsError("write authority constraint role is invalid")

    def _validate_identity_and_scope(self) -> None:
        if self.authority_source != "objective_authority_grammar":
            raise TaskSemanticsError("authority constraint source is not canonical")
        if self.constraint_kind != "deny":
            raise TaskSemanticsError("unsupported authority constraint kind")
        if not self.production_id or not self.governing_clause:
            raise TaskSemanticsError("authority constraint is incomplete")
        start, end = self.governing_span
        if start < 0 or end <= start or not self.consumed_spans:
            raise TaskSemanticsError("authority constraint span is invalid")
        if not self.full_scope_consumed or self.unresolved_authority_material:
            raise TaskSemanticsError("authority constraint scope is not fully consumed")
        if not isinstance(self.objective_fingerprint, str) or not self.objective_fingerprint:
            raise TaskSemanticsError("authority constraint objective identity is invalid")

    def _validate_predicate(self) -> None:
        if self.predicate_id is None:
            if self.predicate_expected is not None or self.condition is not None:
                raise TaskSemanticsError("authority constraint predicate is incomplete")
            return
        if type(self.predicate_expected) is not bool or self.condition is None:
            raise TaskSemanticsError("authority constraint predicate is incomplete")

    def _seal_or_verify(self) -> None:
        expected_seal = _constraint_integrity_seal(self)
        if self._integrity_seal is None:
            object.__setattr__(self, "_integrity_seal", expected_seal)
        elif not hmac.compare_digest(self._integrity_seal, expected_seal):
            raise TypeError("authority constraint integrity check failed")

    @property
    def objective_identity(self) -> str:
        return self.objective_fingerprint

    @property
    def scope_complete(self) -> bool:
        return self.full_scope_consumed

    @property
    def rule_id(self) -> str:
        return self.production_id

    @property
    def polarity(self) -> str:
        return "prohibited"

    @property
    def prohibited(self) -> bool:
        return True

    @property
    def predicate_identity(self) -> str | None:
        return self.predicate_id

    @property
    def expected_predicate_value(self) -> bool | None:
        return self.predicate_expected

    @property
    def conditional(self) -> bool:
        return self.predicate_id is not None


@dataclass(frozen=True, slots=True)
class ObjectiveAuthorityGrammarResult:
    """Complete bounded authority ledger produced by one grammar pass."""

    objective_fingerprint: str
    positive_proofs: tuple[PositiveAuthorityProof, ...] = ()
    constraints: tuple[AuthorityConstraint, ...] = ()
    complete: bool = False
    _factory_token: object = field(default=None, repr=False, compare=False, kw_only=True)

    def __post_init__(self) -> None:
        if self._factory_token is not _AUTHORITY_RESULT_FACTORY_TOKEN:
            raise TypeError("authority grammar result must be created by the canonical grammar owner")
        if not isinstance(self.objective_fingerprint, str) or not self.objective_fingerprint:
            raise TaskSemanticsError("authority grammar result identity is invalid")
        if type(self.complete) is not bool:
            raise TaskSemanticsError("authority grammar completeness is invalid")
        if any(
            not isinstance(item, PositiveAuthorityProof)
            or item.objective_fingerprint != self.objective_fingerprint
            for item in self.positive_proofs
        ):
            raise TaskSemanticsError("authority grammar result contains invalid positive proof")
        if any(
            not isinstance(item, AuthorityConstraint)
            or item.objective_fingerprint != self.objective_fingerprint
            for item in self.constraints
        ):
            raise TaskSemanticsError("authority grammar result contains invalid constraint")

    @property
    def proofs(self) -> tuple[PositiveAuthorityProof, ...]:
        return self.positive_proofs

    @property
    def parse_complete(self) -> bool:
        return self.complete

    @property
    def fail_closed(self) -> bool:
        return not self.complete


def _constraint_integrity_seal(constraint: AuthorityConstraint) -> str:
    material = repr(
        (
            constraint.effect,
            constraint.target,
            constraint.authority_source,
            constraint.production_id,
            constraint.governing_clause,
            constraint.governing_span,
            constraint.consumed_spans,
            constraint.consumed_tokens,
            constraint.target_role,
            constraint.objective_fingerprint,
            constraint.constraint_kind,
            constraint.predicate_id,
            constraint.predicate_expected,
            constraint.condition,
            constraint.full_scope_consumed,
            constraint.unresolved_authority_material,
        )
    ).encode("utf-8")
    return hmac.new(_PROOF_SEAL_KEY, material, hashlib.sha256).hexdigest()


__all__ = ["AuthorityConstraint", "ObjectiveAuthorityGrammarResult"]

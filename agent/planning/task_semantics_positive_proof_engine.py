"""Canonical construction of positive proofs and admitted effects."""

from __future__ import annotations

import hashlib
from typing import Sequence

from agent.planning.task_semantics_authority_model import (
    _AUTHORITY_RESULT_FACTORY_TOKEN,
    AuthorityConstraint,
    ObjectiveAuthorityGrammarResult,
)
from agent.planning.task_semantics_normalization import _normalize_text
from agent.planning.task_semantics_positive_proof_commands import _parse_fragment
from agent.planning.task_semantics_positive_proof_condition import _parse_conditional
from agent.planning.task_semantics_positive_proof_data import (
    _MAX_OBJECTIVE_CHARS,
    _MAX_SCOPE_CHARS,
)
from agent.planning.task_semantics_positive_proof_lexing import (
    _contains_quoted_command,
    _lexemes,
    _repair_mojibake,
    _single_path,
    _split_conjoined_commands,
    _split_control_segments,
)
from agent.planning.task_semantics_positive_proof_model import (
    _PROOF_FACTORY_TOKEN,
    AuthorizedEffect,
    PositiveAuthorityProof,
    _ConstraintSpec,
    _Predicate,
    _ProofSpec,
)
from agent.planning.task_semantics_types import TaskSemanticsError
from agent.resources.contracts import WORKSPACE_RESOURCE, normalize_resource_id


def objective_authority_fingerprint(objective: str) -> str:
    """Return the stable identity to which every proof is sealed."""

    if not isinstance(objective, str):
        raise TypeError("objective must be textual")
    material = " ".join(_normalize_text(_repair_mojibake(objective)).casefold().split())
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def build_positive_authority_proofs(objective: str) -> tuple[PositiveAuthorityProof, ...]:
    """Project positive proofs from the complete canonical authority parse."""

    return parse_objective_authority(objective).positive_proofs


def parse_objective_authority(objective: str) -> ObjectiveAuthorityGrammarResult:
    """Parse positive proofs and negative constraints in one bounded pass."""

    if not isinstance(objective, str):
        raise TypeError("objective must be textual")
    fingerprint = objective_authority_fingerprint(objective)
    repaired = _repair_mojibake(objective).strip()
    if not repaired or len(repaired) > _MAX_OBJECTIVE_CHARS:
        return _authority_result(fingerprint, (), (), False)
    if _contains_quoted_command(repaired):
        return _authority_result(fingerprint, (), (), False)
    lexemes = _lexemes(repaired)
    if not lexemes:
        return _authority_result(fingerprint, (), (), False)
    conditional = _parse_conditional(lexemes)
    if conditional is not None:
        specs, constraints, complete = conditional
    else:
        specs, constraints, complete = _build_direct_specs(lexemes)
    if not complete:
        return _authority_result(fingerprint, (), (), False)
    return _authority_result(
        fingerprint,
        _materialize_proofs(repaired, lexemes, specs),
        _materialize_constraints(repaired, lexemes, constraints),
        True,
    )


def _authority_result(
    fingerprint: str,
    proofs: tuple[PositiveAuthorityProof, ...],
    constraints: tuple[AuthorityConstraint, ...],
    complete: bool,
) -> ObjectiveAuthorityGrammarResult:
    return ObjectiveAuthorityGrammarResult(
        fingerprint,
        proofs,
        constraints,
        complete,
        _factory_token=_AUTHORITY_RESULT_FACTORY_TOKEN,
    )


def _build_direct_specs(
    lexemes: Sequence,
) -> tuple[tuple[_ProofSpec, ...], tuple[_ConstraintSpec, ...], bool]:
    segment_groups = _split_control_segments(lexemes)
    specs: list[_ProofSpec] = []
    constraints: list[_ConstraintSpec] = []
    complete = True
    for group in segment_groups:
        fallback_target = _single_path(group)
        for fragment in _split_conjoined_commands(group):
            fragment_specs, fragment_constraints, recognized = _parse_fragment(
                fragment,
                fallback_target=fallback_target,
                predicate=None,
            )
            specs.extend(fragment_specs)
            constraints.extend(fragment_constraints)
            complete = complete and recognized
    return tuple(specs), tuple(constraints), complete


def authorized_effect_from_proof(
    proof: PositiveAuthorityProof,
    *,
    objective: str,
) -> AuthorizedEffect:
    """Seal one proof into the admitted representation after identity check."""

    if not isinstance(proof, PositiveAuthorityProof):
        raise TypeError("authorized effect requires canonical proof")
    if proof.objective_fingerprint != objective_authority_fingerprint(objective):
        raise TaskSemanticsError("positive authority proof belongs to another objective")
    if proof not in build_positive_authority_proofs(objective):
        raise TaskSemanticsError("positive authority proof is not canonical for this objective")
    return AuthorizedEffect(proof, _factory_token=_PROOF_FACTORY_TOKEN)


def _materialize_proofs(
    objective: str,
    lexemes: Sequence,
    specs: Sequence[_ProofSpec],
) -> tuple[PositiveAuthorityProof, ...]:
    start = lexemes[0].start
    end = lexemes[-1].end
    clause = objective[start:end].strip()
    if not clause or len(clause) > _MAX_SCOPE_CHARS:
        return ()
    fingerprint = objective_authority_fingerprint(objective)
    consumed = tuple(item.value for item in lexemes if item.value not in {",", ".", ":", "!", "?"})
    span = (start, end)
    result: list[PositiveAuthorityProof] = []
    seen: set[tuple[object, ...]] = set()
    for spec in specs:
        target = normalize_resource_id(spec.target)
        identity = (
            spec.effect,
            target,
            spec.predicate.predicate_id if spec.predicate else None,
            spec.predicate.expected if spec.predicate else None,
        )
        if target == WORKSPACE_RESOURCE or identity in seen:
            continue
        seen.add(identity)
        predicate: _Predicate | None = spec.predicate
        result.append(
            PositiveAuthorityProof(
                effect=spec.effect,
                target=target,
                authority_source="objective_positive_grammar",
                production_id=spec.production_id,
                governing_clause=clause,
                governing_span=span,
                consumed_spans=(span,),
                consumed_tokens=consumed,
                target_role=spec.target_role,
                objective_fingerprint=fingerprint,
                predicate_id=predicate.predicate_id if predicate else None,
                predicate_expected=predicate.expected if predicate else None,
                condition=predicate.condition if predicate else None,
                _factory_token=_PROOF_FACTORY_TOKEN,
            )
        )
    return tuple(result)


def _materialize_constraints(
    objective: str,
    lexemes: Sequence,
    specs: Sequence[_ConstraintSpec],
) -> tuple[AuthorityConstraint, ...]:
    if not specs:
        return ()
    start = lexemes[0].start
    end = lexemes[-1].end
    clause = objective[start:end].strip()
    if not clause or len(clause) > _MAX_SCOPE_CHARS:
        return ()
    fingerprint = objective_authority_fingerprint(objective)
    consumed = tuple(item.value for item in lexemes if item.value not in {",", ".", ":", "!", "?"})
    span = (start, end)
    result: list[AuthorityConstraint] = []
    seen: set[tuple[object, ...]] = set()
    for spec in specs:
        target = normalize_resource_id(spec.target)
        predicate: _Predicate | None = spec.predicate
        identity = (
            spec.effect,
            target,
            spec.production_id,
            predicate.predicate_id if predicate else None,
            predicate.expected if predicate else None,
        )
        if identity in seen:
            continue
        seen.add(identity)
        result.append(
            AuthorityConstraint(
                effect=spec.effect,
                target=target,
                authority_source="objective_authority_grammar",
                production_id=spec.production_id,
                governing_clause=clause,
                governing_span=span,
                consumed_spans=(span,),
                consumed_tokens=consumed,
                target_role=spec.target_role,
                objective_fingerprint=fingerprint,
                predicate_id=predicate.predicate_id if predicate else None,
                predicate_expected=predicate.expected if predicate else None,
                condition=predicate.condition if predicate else None,
                _factory_token=_PROOF_FACTORY_TOKEN,
            )
        )
    return tuple(result)


__all__ = [
    "authorized_effect_from_proof",
    "build_positive_authority_proofs",
    "objective_authority_fingerprint",
    "parse_objective_authority",
]

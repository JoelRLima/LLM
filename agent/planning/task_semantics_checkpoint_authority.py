"""Checkpoint authority manifest validation and objective revalidation."""

from __future__ import annotations

from typing import Any, Mapping

from agent.planning.task_semantics_authority import EffectAuthority, admit_effect_authority
from agent.planning.task_semantics_positive_proof import PositiveAuthorityProof
from agent.planning.task_semantics_types import EffectIntent, TaskSemanticsError


def effect_authority_checkpoint(owner: Any) -> dict[str, Any]:
    authority = getattr(owner, "effect_authority", None)
    if isinstance(authority, EffectAuthority):
        return {
            "mode": "objective_positive",
            "proof_schema_version": 1,
            "objective_fingerprint": (
                authority.positive_authority_proofs[0].objective_fingerprint
                if authority.positive_authority_proofs
                else None
            ),
            "proofs": [
                positive_proof_checkpoint(proof)
                for proof in authority.positive_authority_proofs
            ],
        }
    mode = getattr(owner, "_authority_mode", "structured")
    if mode not in {"objective", "structured"}:
        mode = "structured"
    return {"mode": mode}


def restore_effect_authority(
    objective: str,
    requested: tuple[str, ...],
    raw_intents: tuple[EffectIntent, ...],
    raw_authority: Any,
) -> tuple[EffectAuthority | None, tuple[EffectIntent, ...], tuple[EffectIntent, ...]]:
    mode = _authority_mode(requested, raw_intents, raw_authority)
    if mode == "structured":
        validate_nonproof_authority_manifest(raw_authority)
        return None, raw_intents, ()
    if mode == "objective":
        validate_nonproof_authority_manifest(raw_authority)
        if requested or any(item.polarity == "requested" for item in raw_intents):
            raise TaskSemanticsError("checkpoint objetivo nao possui autoridade positiva")
        return None, raw_intents, ()

    authority = admit_effect_authority(objective)
    validate_positive_authority_manifest(raw_authority, authority)
    if requested != authority.requested_effects:
        raise TaskSemanticsError(
            "efeitos solicitados do checkpoint nao correspondem a autoridade positiva"
        )
    expected = authority.admitted_intents
    if raw_intents:
        expected_keys = {_effect_intent_identity(item) for item in expected}
        actual_keys = {_effect_intent_identity(item) for item in raw_intents}
        if actual_keys != expected_keys:
            raise TaskSemanticsError(
                "effect_intents do checkpoint nao correspondem a autoridade canonica"
            )
        effect_intents = raw_intents
    else:
        effect_intents = expected
    candidate_intents = tuple(item.candidate for item in authority.decisions)
    return authority, effect_intents, candidate_intents


def _authority_mode(
    requested: tuple[str, ...],
    raw_intents: tuple[EffectIntent, ...],
    raw_authority: Any,
) -> str:
    if raw_authority is None:
        return (
            "objective_positive"
            if requested or any(item.polarity == "requested" for item in raw_intents)
            else "structured"
        )
    if isinstance(raw_authority, Mapping) and raw_authority.get("mode") in {
        "objective_positive",
        "objective",
        "structured",
    }:
        return str(raw_authority["mode"])
    raise TaskSemanticsError("manifesto de autoridade de efeito invalido")


def positive_proof_checkpoint(proof: PositiveAuthorityProof) -> dict[str, Any]:
    return {
        "effect": proof.effect,
        "target": proof.target,
        "authority_source": proof.authority_source,
        "production_id": proof.production_id,
        "governing_span": list(proof.governing_span),
        "consumed_spans": [list(span) for span in proof.consumed_spans],
        "consumed_tokens": list(proof.consumed_tokens),
        "target_role": proof.target_role,
        "predicate_id": proof.predicate_id,
        "predicate_expected": proof.predicate_expected,
        "condition": proof.condition,
        "approval_reference": proof.approval_reference,
        "objective_fingerprint": proof.objective_fingerprint,
        "full_scope_consumed": proof.full_scope_consumed,
        "unresolved_authority_material": list(proof.unresolved_authority_material),
    }


def validate_positive_authority_manifest(
    raw_authority: Any,
    authority: EffectAuthority,
) -> None:
    if not isinstance(raw_authority, Mapping):
        return
    allowed = {"mode", "proof_schema_version", "objective_fingerprint", "proofs"}
    if set(raw_authority) - allowed:
        raise TaskSemanticsError("manifesto de prova de autoridade contem campos invalidos")
    has_proof_data = any(
        key in raw_authority
        for key in ("proof_schema_version", "objective_fingerprint", "proofs")
    )
    if not has_proof_data:
        return
    if raw_authority.get("proof_schema_version") != 1:
        raise TaskSemanticsError("versao de prova de autoridade invalida")
    raw_proofs = raw_authority.get("proofs")
    if not isinstance(raw_proofs, list) or any(
        not isinstance(item, Mapping) for item in raw_proofs
    ):
        raise TaskSemanticsError("provas de autoridade do checkpoint invalidas")
    expected = [positive_proof_checkpoint(proof) for proof in authority.positive_authority_proofs]
    expected_fingerprint = (
        authority.positive_authority_proofs[0].objective_fingerprint
        if authority.positive_authority_proofs
        else None
    )
    if (
        raw_authority.get("objective_fingerprint") != expected_fingerprint
        or [dict(item) for item in raw_proofs] != expected
    ):
        raise TaskSemanticsError(
            "provas de autoridade do checkpoint nao correspondem ao objetivo"
        )


def validate_nonproof_authority_manifest(raw_authority: Any) -> None:
    if raw_authority is None:
        return
    if not isinstance(raw_authority, Mapping) or set(raw_authority) != {"mode"}:
        raise TaskSemanticsError("manifesto de autoridade sem prova contem campos invalidos")


def _effect_intent_identity(item: EffectIntent) -> tuple[Any, ...]:
    return (
        item.effect,
        item.target,
        item.polarity,
        item.condition,
        item.source,
        item.predicate_id,
        item.predicate_expected,
    )


__all__ = [
    "effect_authority_checkpoint",
    "restore_effect_authority",
    "validate_positive_authority_manifest",
]

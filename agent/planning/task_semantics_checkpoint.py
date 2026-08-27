"""Checkpoint projection and restoration for canonical task semantics."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from agent.planning.task_semantics_checkpoint_authority import (
    effect_authority_checkpoint,
    restore_effect_authority,
)
from agent.planning.task_semantics_types import (
    AdmissionSource,
    EffectIntent,
    ObligationStatus,
    TaskIntent,
    TaskObligation,
    TaskSemanticsError,
    _normalize_effect,
    validate_closed_obligation,
)

TASK_SEMANTICS_SCHEMA_VERSION = 2


def snapshot(owner: Any) -> tuple[dict[str, Any], ...]:
    return tuple(
        {
            **item.to_dict(),
            "status": owner._statuses[item.id].value,
            "evidence_refs": list(owner._evidence[item.id]),
        }
        for item in owner._obligations
    )


def to_checkpoint_dict(owner: Any) -> dict[str, Any]:
    statuses = {key: value.value for key, value in owner._statuses.items()}
    statuses.update({key: value.value for key, value in getattr(owner, "_status_claims", {}).items()})
    evidence = {key: list(value) for key, value in owner._evidence.items() if value}
    evidence.update(
        {
            key: list(value)
            for key, value in getattr(owner, "_evidence_claims", {}).items()
            if value
        }
    )
    return {
        "schema_version": TASK_SEMANTICS_SCHEMA_VERSION,
        "objective": owner.objective,
        "requested_effects": list(owner.requested_effects),
        "prohibited_effects": list(owner.prohibited_effects),
        "effect_authority": effect_authority_checkpoint(owner),
        "effect_intents": [_effect_intent_checkpoint(item) for item in getattr(owner, "effect_intents", ())],
        "predicate_resolutions": {
            predicate_id: {
                "state": evidence.state.value,
                "evidence_ref": evidence.evidence_ref,
                "provenance": evidence.provenance,
            }
            for predicate_id, evidence in getattr(owner, "predicate_resolutions", {}).items()
        },
        "executed_effects": list(owner.executed_effects()),
        "waived_effects": list(owner.waived_effects()),
        "obligations": [item.to_dict() for item in owner._obligations],
        "admission_integrity": _admission_integrity(owner),
        "statuses": statuses,
        "evidence": evidence,
    }


def _effect_intent_checkpoint(item: EffectIntent) -> dict[str, Any]:
    return {
        "effect": item.effect,
        "target": item.target,
        "polarity": item.polarity,
        "condition": item.condition,
        "source": item.source,
        "predicate_id": item.predicate_id,
        "predicate_expected": item.predicate_expected,
        "predicate_state": item.predicate_state.value,
        "predicate_evidence_ref": item.predicate_evidence_ref,
        "predicate_provenance": item.predicate_provenance,
        "candidate_role": item.candidate_role,
        "positive_syntax": item.positive_syntax,
    }


def restore_from_checkpoint(cls: Any, data: Mapping[str, Any]) -> Any:
    if not isinstance(data, Mapping):
        raise TaskSemanticsError("task semantics ausente ou invalido")
    objective = data.get("objective")
    raw_obligations = data.get("obligations")
    if data.get("schema_version") != TASK_SEMANTICS_SCHEMA_VERSION:
        raise TaskSemanticsError("checkpoint de task semantics sem versao inequívoca")
    if not isinstance(objective, str) or not isinstance(raw_obligations, list):
        raise TaskSemanticsError("checkpoint sem contrato semantico valido")
    _validate_admission_integrity(raw_obligations, data.get("admission_integrity"))
    definitions = [_obligation_from_checkpoint(raw) for raw in raw_obligations]
    requested = tuple(
        dict.fromkeys(_normalize_effect(item) for item in (data.get("requested_effects") or []))
    )
    prohibited = tuple(
        dict.fromkeys(_normalize_effect(item) for item in (data.get("prohibited_effects") or []))
    )
    raw_intents = data.get("effect_intents")
    if raw_intents is None:
        effect_intents: tuple[EffectIntent, ...] = ()
    elif isinstance(raw_intents, list):
        effect_intents = tuple(_effect_intent_from_checkpoint(item) for item in raw_intents)
    else:
        raise TaskSemanticsError("checkpoint contem effect_intents invalidos")
    effect_authority, effect_intents, candidate_intents = restore_effect_authority(
        objective,
        requested,
        effect_intents,
        data.get("effect_authority"),
    )
    semantics = cls(
        TaskIntent(objective, requested, prohibited, effect_intents=effect_intents),
        definitions,
        statuses=data.get("statuses"),
        evidence=data.get("evidence"),
        predicate_resolutions=data.get("predicate_resolutions"),
        effect_authority=effect_authority,
        candidate_effect_intents=candidate_intents,
        executed_effects=(),
        waived_effects=(),
        _strict_evidence=True,
    )
    for obligation in semantics.obligations:
        if (
            obligation.kind == "effect"
            and semantics.obligation_status(obligation.id) is not ObligationStatus.PENDING
            and any(
                isinstance(ref, str) and ref.startswith("legacy:")
                for ref in semantics.obligation_evidence(obligation.id)
            )
        ):
            raise TaskSemanticsError("evidencia sintetica nao pode provar efeito operacional")
    return semantics


def _effect_intent_from_checkpoint(raw: Any) -> EffectIntent:
    if not isinstance(raw, Mapping):
        raise TaskSemanticsError("checkpoint contem effect_intent invalido")
    try:
        return EffectIntent(
            effect=raw["effect"],
            target=raw.get("target", "*"),
            polarity=raw.get("polarity", "requested"),
            condition=raw.get("condition"),
            source=raw.get("source", "objective"),
            predicate_id=raw.get("predicate_id"),
            predicate_expected=raw.get("predicate_expected"),
            predicate_state=raw.get("predicate_state", "UNRESOLVED"),
            predicate_evidence_ref=raw.get("predicate_evidence_ref"),
            predicate_provenance=raw.get("predicate_provenance"),
            candidate_role=raw.get("candidate_role", "UNKNOWN"),
            positive_syntax=raw.get("positive_syntax", False),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise TaskSemanticsError("checkpoint contem effect_intent invalido") from exc


def _obligation_from_checkpoint(raw: Any) -> TaskObligation:
    if not isinstance(raw, Mapping):
        raise TaskSemanticsError("checkpoint contem obrigacao invalida")
    identifier = raw.get("id")
    kind = raw.get("kind")
    description = raw.get("description")
    if not isinstance(identifier, str) or not isinstance(kind, str) or not isinstance(description, str):
        raise TaskSemanticsError("checkpoint contem obrigacao incompleta")
    effect = raw.get("effect")
    condition = raw.get("condition")
    if effect is not None and not isinstance(effect, str):
        raise TaskSemanticsError("checkpoint contem efeito de obrigacao invalido")
    if condition is not None and not isinstance(condition, str):
        raise TaskSemanticsError("checkpoint contem condition de obrigacao invalida")
    obligation = TaskObligation(
        id=identifier,
        kind=kind,
        description=description,
        effect=effect,
        condition=condition,
        target=raw.get("target"),
        query=raw.get("query"),
        operands=raw.get("operands", ()),
        fallback_target=raw.get("fallback_target"),
        query_source=raw.get("query_source"),
        admission_source=raw.get("admission_source", AdmissionSource.OBJECTIVE_DERIVED),
        admission_evidence_ref=raw.get("admission_evidence_ref"),
        admission_authorization=raw.get("admission_authorization"),
    )
    validate_closed_obligation(obligation)
    return obligation


def _admission_integrity(owner: Any) -> dict[str, str]:
    return {item.id: _admission_digest(item.to_dict()) for item in owner._obligations}


def _admission_digest(payload: Mapping[str, Any]) -> str:
    material = json.dumps(
        {
            "id": payload.get("id"),
            "kind": payload.get("kind"),
            "target": payload.get("target"),
            "query": payload.get("query"),
            "operands": payload.get("operands"),
            "fallback_target": payload.get("fallback_target"),
            "query_source": payload.get("query_source"),
            "effect": payload.get("effect"),
            "admission_source": payload.get("admission_source"),
            "admission_evidence_ref": payload.get("admission_evidence_ref"),
            "admission_authorization": payload.get("admission_authorization"),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _validate_admission_integrity(raw_obligations: list[Any], raw_integrity: Any) -> None:
    if raw_integrity is None:
        for raw in raw_obligations:
            if not isinstance(raw, Mapping):
                continue
            source = raw.get("admission_source")
            if source is not None and source != AdmissionSource.OBJECTIVE_DERIVED.value:
                raise TaskSemanticsError("checkpoint sem integridade de admissao confiavel")
        return
    if not isinstance(raw_integrity, Mapping):
        raise TaskSemanticsError("integridade de admissao do checkpoint invalida")
    expected: dict[str, str] = {}
    for raw in raw_obligations:
        if not isinstance(raw, Mapping) or not isinstance(raw.get("id"), str):
            raise TaskSemanticsError("checkpoint contem obrigacao incompleta")
        digest = raw_integrity.get(raw["id"])
        if not isinstance(digest, str) or digest != _admission_digest(raw):
            raise TaskSemanticsError("proveniencia de admissao do checkpoint nao confere")
        expected[raw["id"]] = digest
    if set(raw_integrity) != set(expected):
        raise TaskSemanticsError("integridade de admissao do checkpoint incompleta")


__all__ = ["TASK_SEMANTICS_SCHEMA_VERSION", "snapshot", "to_checkpoint_dict", "restore_from_checkpoint"]

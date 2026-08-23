"""Normalization and closed-shape validation for durable obligations."""

from __future__ import annotations

from typing import Any


def _error(message: str) -> Exception:
    from agent.planning.task_semantics_types import TaskSemanticsError

    return TaskSemanticsError(message)


def _normalize_authorization(authorization: Any) -> str | None:
    if authorization is None:
        return None
    if not isinstance(authorization, str):
        raise _error("autorizacao de admissao da obrigacao invalida")
    normalized = " ".join(authorization.split())
    if not normalized or len(normalized) > 240:
        raise _error("autorizacao de admissao da obrigacao invalida")
    return normalized


def _validate_evidence_admission(ref: int | str | None, authorization: str | None) -> None:
    if ref is None:
        raise _error("admissao derivada de evidencia requer referencia canonica")
    if authorization is not None:
        raise _error("admissao por evidencia nao aceita autorizacao externa")


def _validate_objective_admission(ref: int | str | None, authorization: str | None) -> None:
    if ref is not None or authorization is not None:
        raise _error("admissao derivada do objetivo nao aceita referencia externa")


def _validate_safety_admission(ref: int | str | None, authorization: str | None) -> None:
    if ref is not None or not authorization:
        raise _error("admissao de seguranca requer autoridade runtime")
    if not authorization.startswith("runtime:safety:"):
        raise _error("autoridade de seguranca invalida")


def _validate_external_admission(ref: int | str | None, authorization: str | None) -> None:
    if ref is not None or not authorization:
        raise _error("admissao externa requer autorizacao explicita")
    if not authorization.startswith("external:"):
        raise _error("autorizacao externa invalida")


def normalize_admission_fields(
    source: Any,
    evidence_ref: Any,
    authorization: Any,
) -> tuple[Any, int | str | None, str | None]:
    from agent.planning.task_semantics_types import (
        AdmissionSource,
        _eligible_evidence_ref,
        _normalize_admission_source,
    )

    normalized_source = _normalize_admission_source(source)
    normalized_ref = None if evidence_ref is None else _eligible_evidence_ref(evidence_ref)
    normalized_authorization = _normalize_authorization(authorization)
    validators = {
        AdmissionSource.CANONICAL_EVIDENCE_DERIVED: _validate_evidence_admission,
        AdmissionSource.OBJECTIVE_DERIVED: _validate_objective_admission,
        AdmissionSource.SAFETY_REQUIRED: _validate_safety_admission,
        AdmissionSource.EXTERNALLY_AUTHORIZED: _validate_external_admission,
    }
    validator = validators.get(normalized_source)
    if validator is not None:
        validator(normalized_ref, normalized_authorization)
    return normalized_source, normalized_ref, normalized_authorization


def obligation_to_dict(item: Any) -> dict[str, Any]:
    return {
        "id": item.id,
        "kind": item.kind,
        "description": item.description,
        **({"effect": item.effect} if item.effect is not None else {}),
        **({"condition": item.condition} if item.condition is not None else {}),
        **({"target": item.target} if item.target is not None else {}),
        **({"query": item.query} if item.query is not None else {}),
        **({"operands": list(item.operands)} if item.operands else {}),
        **({"fallback_target": item.fallback_target} if item.fallback_target is not None else {}),
        **({"query_source": item.query_source} if item.query_source is not None else {}),
        "admission_source": item.admission_source.value,
        **(
            {"admission_evidence_ref": item.admission_evidence_ref}
            if item.admission_evidence_ref is not None
            else {}
        ),
        **(
            {"admission_authorization": item.admission_authorization}
            if item.admission_authorization is not None
            else {}
        ),
    }


def _validate_effect(item: Any) -> None:
    if any(value for value in (item.target, item.query, item.operands, item.fallback_target, item.query_source)):
        raise _error("obrigacao de efeito contem identidade invalida")


def _validate_read(item: Any) -> None:
    if item.target is None or any(
        value for value in (item.query, item.operands, item.fallback_target, item.query_source)
    ):
        raise _error("obrigacao read requer target exclusivo")


def _validate_search(item: Any) -> None:
    if (item.query is None) == (item.query_source is None):
        raise _error("obrigacao search requer query ou query_source exclusivo")
    if item.operands or item.fallback_target is not None:
        raise _error("obrigacao search contem identidade invalida")


def _validate_compare(item: Any) -> None:
    if len(item.operands) != 2 or any(
        value is not None for value in (item.target, item.query, item.fallback_target, item.query_source)
    ):
        raise _error("obrigacao compare requer exatamente dois operands")


def _validate_analyze(item: Any) -> None:
    if item.target is None and item.query is None:
        raise _error("obrigacao analyze requer target ou query")
    if item.operands or item.fallback_target is not None or item.query_source is not None:
        raise _error("obrigacao analyze contem identidade invalida")


def _validate_fallback(item: Any) -> None:
    if (
        item.fallback_target is None
        or item.target is not None
        or item.query is not None
        or item.operands
        or item.query_source is not None
    ):
        raise _error("obrigacao fallback requer fallback_target exclusivo")


def validate_closed_obligation(item: Any) -> None:
    from agent.planning.task_semantics_types import OBLIGATION_KINDS, TaskObligation, TaskSemanticsError

    if not isinstance(item, TaskObligation):
        raise TaskSemanticsError("obrigacao invalida")
    validators = {
        "effect": _validate_effect,
        "read": _validate_read,
        "search": _validate_search,
        "compare": _validate_compare,
        "analyze": _validate_analyze,
        "fallback": _validate_fallback,
    }
    if item.kind not in OBLIGATION_KINDS or item.kind not in validators:
        raise TaskSemanticsError("kind da obrigacao nao suportado")
    validators[item.kind](item)


__all__ = ["normalize_admission_fields", "obligation_to_dict", "validate_closed_obligation"]

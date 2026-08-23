"""Bounded canonical review of model-proposed task obligations."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, replace
from typing import Any

from agent.planning.task_semantics_admission import (
    derive_admission,
    semantic_identity,
    validate_admission_provenance,
)
from agent.planning.task_semantics_admission import (
    trusted_authorization as normalize_trusted_authorization,
)
from agent.planning.task_semantics_types import (
    MAX_OBLIGATIONS,
    MAX_REVIEW_OBLIGATIONS,
    AdmissionSource,
    ObligationStatus,
    TaskObligation,
    TaskSemanticsError,
    _normalize_text,
    validate_closed_obligation,
)

_OBLIGATION_KEYS = frozenset(
    {
        "id",
        "kind",
        "description",
        "effect",
        "condition",
        "target",
        "query",
        "operands",
        "fallback_target",
        "query_source",
    }
)
_FORBIDDEN_KEYS = frozenset(
    {
        "status",
        "terminal",
        "success",
        "succeeded",
        "completed",
        "satisfied",
        "waived",
        "blocked",
        "result",
        "data",
        "tool",
        "instructions",
        "admission_source",
        "admission_evidence_ref",
        "admission_authorization",
    }
)
_REVIEW_SOURCES = frozenset({"initial_plan", "canonical_review"})


@dataclass(frozen=True, slots=True)
class ObligationRejection:
    """An untrusted proposal rejected without entering durable semantics."""

    proposal: Any
    reason: str
    code: str

    @property
    def raw(self) -> Any:
        return self.proposal

    def to_dict(self) -> dict[str, Any]:
        return {"proposal": self.proposal, "reason": self.reason, "code": self.code}


@dataclass(frozen=True, slots=True)
class ObligationReviewResult:
    """Atomic accepted/rejected view of one bounded review request."""

    accepted: tuple[TaskObligation, ...] = ()
    rejected: tuple[ObligationRejection, ...] = ()

    @property
    def accepted_obligations(self) -> tuple[TaskObligation, ...]:
        return self.accepted

    @property
    def rejected_obligations(self) -> tuple[ObligationRejection, ...]:
        return self.rejected

    @property
    def reasons(self) -> tuple[str, ...]:
        return tuple(item.reason for item in self.rejected)

    @property
    def added(self) -> int:
        return len(self.accepted)

    def __iter__(self) -> Iterator[TaskObligation]:
        return iter(self.accepted)

    def __len__(self) -> int:
        return len(self.accepted)

    def __getitem__(self, index: int) -> TaskObligation:
        return self.accepted[index]


class _AdmissionRejected(TaskSemanticsError):
    def __init__(self, reason: str, code: str) -> None:
        super().__init__(reason)
        self.code = code


def _rejection_code(error: Exception) -> str:
    explicit = getattr(error, "code", None)
    if isinstance(explicit, str):
        return explicit
    reason = str(error)
    for marker, code in (
        ("conexao deterministica", "UNRELATED_OBJECTIVE"),
        ("nao esta ligada ao objetivo", "UNRELATED_OBJECTIVE"),
        ("previous_read requer", "MISSING_CAUSAL_EVIDENCE"),
        ("corresponde a evidencia canonica", "MISSING_CAUSAL_EVIDENCE"),
        ("corresponde a leitura canonica", "MISSING_CAUSAL_EVIDENCE"),
    ):
        if marker in reason:
            return code
    return "INVALID_OBLIGATION"


def review_and_add(
    owner: Any,
    raw: Any,
    *,
    source: str,
    collect_rejections: bool = False,
    trusted_admission: AdmissionSource | None = None,
    trusted_authorization: str | None = None,
) -> tuple[TaskObligation, ...] | ObligationReviewResult:
    """Review untrusted proposals, or add through one explicit trusted path.

    Strict return mode preserves the historical exception-based API.  Report
    mode is the model boundary: each rejected proposal is returned, and only
    accepted candidates are committed.
    """

    if not isinstance(source, str) or (source not in _REVIEW_SOURCES and trusted_admission is None):
        raise TaskSemanticsError("obrigacoes so podem entrar por revisao canonica")
    if trusted_admission is not None and not isinstance(trusted_admission, AdmissionSource):
        raise TaskSemanticsError("autoridade de admissao invalida")
    if not isinstance(raw, (list, tuple)) or len(raw) > MAX_REVIEW_OBLIGATIONS:
        rejection = ObligationRejection(
            raw,
            "payload de obrigacoes invalido ou ilimitado",
            "INVALID_REVIEW_PAYLOAD",
        )
        if not collect_rejections:
            raise TaskSemanticsError(rejection.reason)
        return ObligationReviewResult(rejected=(rejection,))

    candidates: list[TaskObligation] = []
    rejected: list[ObligationRejection] = []
    seen = set(owner._statuses)
    existing_identities = {semantic_identity(item) for item in owner._obligations}
    for item in raw:
        try:
            candidate = _candidate(
                item,
                owner,
                seen,
                trusted_admission=trusted_admission,
                trusted_authorization=trusted_authorization,
            )
            identity = semantic_identity(candidate)
            if identity in existing_identities:
                raise _AdmissionRejected("obrigacao equivalente ja existe", "DUPLICATE_OBLIGATION")
            existing_identities.add(identity)
            candidates.append(candidate)
        except (TaskSemanticsError, TypeError, ValueError) as exc:
            rejected.append(ObligationRejection(item, str(exc), _rejection_code(exc)))

    available = MAX_OBLIGATIONS - len(owner._obligations)
    if len(candidates) > available:
        overflow = candidates[available:]
        candidates = candidates[:available]
        rejected.extend(
            ObligationRejection(item.to_dict(), "limite de obrigacoes excedido", "OBLIGATION_LIMIT")
            for item in overflow
        )

    result = ObligationReviewResult(tuple(candidates), tuple(rejected))
    if rejected and not collect_rejections:
        raise TaskSemanticsError(rejected[0].reason)
    _commit(owner, candidates)
    return result if collect_rejections else result.accepted


def _commit(owner: Any, candidates: list[TaskObligation]) -> None:
    owner._obligations = owner._obligations + tuple(candidates)
    for item in candidates:
        owner._statuses[item.id] = ObligationStatus.PENDING
        owner._evidence[item.id] = []


def _candidate(
    item: Any,
    owner: Any,
    seen: set[str],
    *,
    trusted_admission: AdmissionSource | None,
    trusted_authorization: str | None,
) -> TaskObligation:
    if not isinstance(item, Mapping):
        raise TaskSemanticsError("obrigacao de modelo deve ser objeto")
    keys = set(item)
    if keys & _FORBIDDEN_KEYS or not keys.issubset(_OBLIGATION_KEYS):
        raise TaskSemanticsError("payload de obrigacao contem autoridade proibida")
    kind = item.get("kind")
    description = item.get("description")
    if not isinstance(kind, str) or not isinstance(description, str):
        raise TaskSemanticsError("obrigacao requer kind e description")
    effect = item.get("effect")
    if kind.casefold() == "effect" and effect not in owner.requested_effects:
        raise TaskSemanticsError("modelo nao pode inventar efeito solicitado")
    identifier = item.get("id")
    if identifier is None:
        identifier = _stable_id(
            kind,
            description,
            effect if isinstance(effect, str) else None,
            item,
        )
    obligation = TaskObligation(
        id=identifier,
        kind=kind,
        description=description,
        effect=effect,
        condition=item.get("condition"),
        target=item.get("target"),
        query=item.get("query"),
        operands=item.get("operands", ()),
        fallback_target=item.get("fallback_target"),
        query_source=item.get("query_source"),
    )
    validate_closed_obligation(obligation)
    if trusted_admission is not None:
        if trusted_admission is AdmissionSource.CANONICAL_EVIDENCE_DERIVED:
            raise TaskSemanticsError("admissao por evidencia requer evidencia canonica")
        obligation = replace(
            obligation,
            admission_source=trusted_admission,
            admission_authorization=normalize_trusted_authorization(
                trusted_admission, trusted_authorization
            ),
        )
    else:
        admission_source, evidence_ref = derive_admission(owner, obligation)
        obligation = replace(
            obligation,
            admission_source=admission_source,
            admission_evidence_ref=evidence_ref,
        )
    if obligation.id in seen:
        raise TaskSemanticsError("ids de obrigacoes duplicados")
    seen.add(obligation.id)
    return obligation


def _stable_id(kind: str, description: str, effect: str | None, item: Mapping[str, Any]) -> str:
    identity = "|".join(
        str(item.get(key, ""))
        for key in ("target", "query", "operands", "fallback_target", "query_source")
    )
    material = f"{kind}|{effect or ''}|{identity}|{_normalize_text(description)}"
    return f"requirement:{kind}:{hashlib.sha256(material.encode('utf-8')).hexdigest()[:16]}"


__all__ = [
    "ObligationRejection",
    "ObligationReviewResult",
    "review_and_add",
    "validate_admission_provenance",
]

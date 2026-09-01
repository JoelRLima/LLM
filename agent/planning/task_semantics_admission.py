"""Trusted admission provenance and canonical evidence matching."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from agent.planning.task_semantics_evidence import (
    _READ_TOOLS,
    arg_path,
    exact_source_observation,
    same_identity,
)
from agent.planning.task_semantics_inference import infer_effect_semantics, inferred_obligations
from agent.planning.task_semantics_types import (
    AdmissionSource,
    TaskObligation,
    TaskSemanticsError,
)
from agent.runtime.failure_policy import FailureClass, classify_failure


def semantic_identity(item: TaskObligation) -> tuple[Any, ...]:
    """Identify a bounded requirement independently of model prose or id."""

    def identity_text(value: str | None) -> str | None:
        return value.casefold() if isinstance(value, str) else value

    if item.kind == "effect":
        return (item.kind, identity_text(item.effect))
    if item.kind == "read":
        return (item.kind, identity_text(item.target))
    if item.kind == "search":
        return (item.kind, identity_text(item.query), item.query_source)
    if item.kind == "compare":
        return (
            item.kind,
            tuple(sorted((identity_text(value) for value in item.operands), key=str.casefold)),
        )
    if item.kind == "analyze":
        return (item.kind, identity_text(item.target), identity_text(item.query))
    if item.kind == "fallback":
        return (item.kind, identity_text(item.fallback_target))
    return (item.kind,)


def trusted_authorization(source: AdmissionSource, authorization: str | None) -> str:
    if not isinstance(authorization, str) or not authorization.strip():
        raise TaskSemanticsError("admissao confiavel requer autorizacao explicita")
    normalized = " ".join(authorization.split())
    if source is AdmissionSource.SAFETY_REQUIRED:
        return normalized if normalized.startswith("runtime:safety:") else f"runtime:safety:{normalized}"
    if source is AdmissionSource.EXTERNALLY_AUTHORIZED:
        return normalized if normalized.startswith("external:") else f"external:{normalized}"
    raise TaskSemanticsError("origem de admissao confiavel nao suportada")


def derive_admission(owner: Any, obligation: TaskObligation) -> tuple[AdmissionSource, int | str | None]:
    if obligation.kind == "fallback":
        evidence_ref = fallback_evidence_ref(owner, obligation.fallback_target)
        if evidence_ref is None:
            raise TaskSemanticsError(
                "fallback requer falha local canonica do read correspondente"
            )
        if not has_required_read(owner, obligation.fallback_target):
            raise TaskSemanticsError("fallback nao esta ligado a um read requerido")
        return AdmissionSource.CANONICAL_EVIDENCE_DERIVED, evidence_ref

    if obligation.kind == "search" and obligation.query_source == "previous_read":
        evidence_ref = previous_read_evidence_ref(owner)
        if evidence_ref is None:
            raise TaskSemanticsError("busca previous_read requer leitura canonica causal")
        if not objective_identity_exists(owner, obligation):
            raise TaskSemanticsError("busca nao esta ligada ao objetivo original")
        return AdmissionSource.CANONICAL_EVIDENCE_DERIVED, evidence_ref

    if objective_identity_exists(owner, obligation):
        return AdmissionSource.OBJECTIVE_DERIVED, None
    raise TaskSemanticsError(
        "obrigacao nao possui conexao deterministica com o objetivo original"
    )


def objective_identity_exists(owner: Any, obligation: TaskObligation) -> bool:
    if obligation.kind == "effect":
        authority = getattr(owner, "effect_authority", None)
        if authority is not None:
            return any(
                item.effect == obligation.effect
                for item in getattr(authority, "authorized_effects", ())
            )
        # A directly constructed TaskIntent is an explicit internal control
        # contract, not a free-form semantic candidate.  Keep this narrow
        # compatibility path separate from objective-derived admission.
        if getattr(owner, "_authority_mode", None) == "legacy":
            return False
        return any(
            getattr(item, "polarity", None) == "requested"
            and getattr(item, "effect", None) == obligation.effect
            and str(getattr(item, "source", "")).casefold() != "model"
            for item in getattr(owner, "effect_intents", ())
        )
    try:
        canonical = inferred_obligations(
            owner.objective,
            infer_effect_semantics(owner.objective),
        )
    except (TypeError, ValueError, TaskSemanticsError):
        return False
    identity = semantic_identity(obligation)
    return any(semantic_identity(item) == identity for item in canonical)


def has_required_read(owner: Any, target: str | None) -> bool:
    if target is None:
        return False
    if any(item.kind == "read" and same_identity(item.target, target) for item in owner._obligations):
        return True
    try:
        canonical = inferred_obligations(
            owner.objective,
            infer_effect_semantics(owner.objective),
        )
    except (TypeError, ValueError, TaskSemanticsError):
        return False
    return any(item.kind == "read" and same_identity(item.target, target) for item in canonical)


def fallback_evidence_ref(owner: Any, target: str | None) -> int | str | None:
    if target is None:
        return None
    for ref, entry in sorted(
        getattr(owner, "_evidence_catalog", {}).items(),
        key=lambda pair: (0, pair[0]) if type(pair[0]) is int else (1, str(pair[0])),
    ):
        if not isinstance(entry, Mapping) or entry.get("tool") not in _READ_TOOLS:
            continue
        args = entry.get("args")
        result = entry.get("result")
        if (
            isinstance(result, Mapping)
            and same_identity(arg_path(args), target)
            and classify_failure(result) is FailureClass.LOCAL
        ):
            return cast(int | str, ref)
    return None


def previous_read_evidence_ref(owner: Any) -> int | str | None:
    required_targets = {
        item.target.casefold()
        for item in owner._obligations
        if item.kind == "read" and isinstance(item.target, str)
    }
    if not required_targets:
        try:
            required_targets = {
                item.target.casefold()
                for item in inferred_obligations(
                    owner.objective,
                    infer_effect_semantics(owner.objective),
                )
                if item.kind == "read" and isinstance(item.target, str)
            }
        except (TypeError, ValueError, TaskSemanticsError):
            required_targets = set()
    for ref, entry in sorted(
        getattr(owner, "_evidence_catalog", {}).items(),
        key=lambda pair: (0, pair[0]) if type(pair[0]) is int else (1, str(pair[0])),
    ):
        entry_path = (
            arg_path(entry.get("args"))
            if isinstance(entry, Mapping) and isinstance(entry.get("args"), Mapping)
            else None
        )
        if (
            isinstance(entry, Mapping)
            and entry.get("tool") in _READ_TOOLS
            and (
                not required_targets
                or (isinstance(entry_path, str) and entry_path.casefold() in required_targets)
            )
            and isinstance(entry.get("result"), Mapping)
            and exact_source_observation(entry["result"])
        ):
            return cast(int | str, ref)
    return None


def _validate_evidence_admission(owner: Any, obligation: TaskObligation) -> None:
    ref = obligation.admission_evidence_ref
    if obligation.kind == "fallback":
        expected_ref = fallback_evidence_ref(owner, obligation.fallback_target)
        if not has_required_read(owner, obligation.fallback_target):
            raise TaskSemanticsError("fallback nao esta ligado a um read requerido")
        if ref is None or expected_ref is None or ref != expected_ref:
            raise TaskSemanticsError("admissao de fallback nao corresponde a evidencia canonica")
        return
    if obligation.kind == "search" and obligation.query_source == "previous_read":
        if not objective_identity_exists(owner, obligation):
            raise TaskSemanticsError("busca nao esta ligada ao objetivo original")
        expected_ref = previous_read_evidence_ref(owner)
        if ref is None or expected_ref is None or ref != expected_ref:
            raise TaskSemanticsError("admissao de busca nao corresponde a leitura canonica")
        return
    raise TaskSemanticsError("tipo de admissao por evidencia invalido")


def _validate_objective_admission(owner: Any, obligation: TaskObligation) -> None:
    if obligation.admission_evidence_ref is not None or obligation.admission_authorization is not None:
        raise TaskSemanticsError("admissao derivada do objetivo contem autoridade externa")
    if not objective_identity_exists(owner, obligation):
        raise TaskSemanticsError("admissao derivada do objetivo nao corresponde ao objetivo original")


def _live_admission_authority_approves(
    authority: Any,
    source: AdmissionSource,
    obligation: TaskObligation,
) -> bool:
    if authority is None:
        return False
    verifier = getattr(authority, "revalidate_admission", None)
    if callable(verifier):
        return bool(verifier(source, obligation))
    if callable(authority):
        return bool(authority(source, obligation))
    return False


def _validate_safety_admission(
    owner: Any,
    obligation: TaskObligation,
    admission_authority: Any,
) -> None:
    if not (obligation.admission_authorization or "").startswith("runtime:safety:"):
        raise TaskSemanticsError("admissao de seguranca sem autoridade runtime")
    if not _live_admission_authority_approves(
        admission_authority,
        AdmissionSource.SAFETY_REQUIRED,
        obligation,
    ):
        raise TaskSemanticsError("admissao de seguranca requer autoridade runtime viva")


def _validate_external_admission(
    owner: Any,
    obligation: TaskObligation,
    admission_authority: Any,
) -> None:
    if not (obligation.admission_authorization or "").startswith("external:"):
        raise TaskSemanticsError("admissao externa sem autorizacao")
    if not _live_admission_authority_approves(
        admission_authority,
        AdmissionSource.EXTERNALLY_AUTHORIZED,
        obligation,
    ):
        raise TaskSemanticsError("admissao externa requer autoridade viva")


def validate_admission_provenance(owner: Any, *, admission_authority: Any = None) -> None:
    """Re-prove evidence-derived admission records against canonical history."""

    for obligation in owner._obligations:
        source = obligation.admission_source
        if source is AdmissionSource.CANONICAL_EVIDENCE_DERIVED:
            _validate_evidence_admission(owner, obligation)
        elif source is AdmissionSource.OBJECTIVE_DERIVED:
            _validate_objective_admission(owner, obligation)
        elif source is AdmissionSource.SAFETY_REQUIRED:
            _validate_safety_admission(owner, obligation, admission_authority)
        elif source is AdmissionSource.EXTERNALLY_AUTHORIZED:
            _validate_external_admission(owner, obligation, admission_authority)
        else:
            raise TaskSemanticsError("origem de admissao desconhecida")


__all__ = [
    "derive_admission",
    "_live_admission_authority_approves",
    "objective_identity_exists",
    "semantic_identity",
    "trusted_authorization",
    "validate_admission_provenance",
]

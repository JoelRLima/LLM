"""Evidence-backed transitions for the canonical task semantic owner."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from agent.planning.failure_policy import FailureClass, classify_failure
from agent.planning.task_semantics_evidence import (
    _READ_TOOLS,
    arg_path,
    complete_observation,
    matches_fallback,
    matches_requirement,
    result_is_successful,
    same_identity,
)
from agent.planning.task_semantics_types import (
    ObligationStatus,
    TaskIntent,
    TaskObligation,
    TaskSemanticsError,
    _eligible_evidence_ref,
    _normalize_effect,
)


def transition(
    owner: Any,
    obligation_id: str,
    status: ObligationStatus,
    *,
    evidence_ref: int | str | None,
    allow_legacy: bool = False,
) -> None:
    if obligation_id not in owner._statuses:
        raise TaskSemanticsError("obrigacao desconhecida")
    ref = None if evidence_ref is None else _eligible_evidence_ref(evidence_ref)
    if ref is not None:
        _require_evidence_provenance(
            owner,
            obligation_id,
            ref,
            allow_legacy=allow_legacy,
        )
    current = owner._statuses[obligation_id]
    if current is not ObligationStatus.PENDING:
        if current is status and ref is not None:
            if ref not in owner._evidence[obligation_id]:
                owner._evidence[obligation_id].append(ref)
            return
        if current is status:
            return
        raise TaskSemanticsError("transicao de obrigacao terminal invalida")
    if ref is None:
        if not allow_legacy:
            raise TaskSemanticsError("transicao operacional requer evidencia")
        ref = f"legacy:{obligation_id}"
    owner._statuses[obligation_id] = status
    owner._evidence[obligation_id].append(ref)


def _require_evidence_provenance(
    owner: Any,
    obligation_id: str,
    evidence_ref: int | str,
    *,
    allow_legacy: bool,
) -> None:
    if allow_legacy or not getattr(owner, "_strict_evidence", False):
        return
    observation = getattr(owner, "_evidence_catalog", {}).get(evidence_ref)
    if observation is None:
        raise TaskSemanticsError("referencia de evidencia nao pertence ao historico canonico")
    obligation = next(
        (item for item in owner._obligations if item.id == obligation_id),
        None,
    )
    if obligation is None:
        raise TaskSemanticsError("obrigacao desconhecida")
    if obligation.kind == "effect":
        return
    result = observation.get("result")
    if not isinstance(result, Mapping) or not _evidence_proves_requirement(
        owner,
        obligation,
        str(observation.get("tool", "")),
        result,
        observation.get("args"),
        evidence_ref,
    ):
        raise TaskSemanticsError("evidencia nao prova a obrigacao especifica")


def _evidence_proves_requirement(
    owner: Any,
    obligation: Any,
    tool: str,
    result: Mapping[str, Any],
    args: Mapping[str, Any] | None,
    evidence_ref: int | str | None = None,
) -> bool:
    if (
        obligation.kind == "read"
        and classify_failure(result) is FailureClass.LOCAL
        and evidence_ref is not None
        and any(
            item.kind == "fallback"
            and owner._statuses[item.id] is ObligationStatus.SATISFIED
            and evidence_ref in owner._evidence[item.id]
            and same_identity(item.fallback_target, arg_path(args))
            for item in owner._obligations
        )
    ):
        return True
    if matches_requirement(owner, obligation, tool, result, args):
        return True
    if obligation.kind == "compare" and complete_observation(result) and tool in _READ_TOOLS:
        return any(same_identity(operand, arg_path(args)) for operand in obligation.operands)
    if obligation.kind == "fallback":
        return matches_fallback(obligation, tool, result, args)
    return False


def satisfy(owner: Any, obligation_id: str, evidence_ref: int | str) -> None:
    transition(owner, obligation_id, ObligationStatus.SATISFIED, evidence_ref=evidence_ref)


def waive(owner: Any, obligation_id: str, evidence_ref: int | str) -> None:
    transition(owner, obligation_id, ObligationStatus.WAIVED, evidence_ref=evidence_ref)


def block(owner: Any, obligation_id: str, evidence_ref: int | str) -> None:
    transition(owner, obligation_id, ObligationStatus.BLOCKED, evidence_ref=evidence_ref)


def record_effect(owner: Any, effect: str, *, evidence_ref: int | str | None, allow_legacy: bool) -> None:
    normalized = _normalize_effect(effect)
    match = next(
        (item for item in owner._obligations if item.kind == "effect" and item.effect == normalized),
        None,
    )
    if match is None:
        if normalized not in owner._executed_effects:
            owner._executed_effects.append(normalized)
        return
    transition(
        owner,
        match.id,
        ObligationStatus.SATISFIED,
        evidence_ref=evidence_ref,
        allow_legacy=allow_legacy,
    )
    if normalized not in owner._executed_effects:
        owner._executed_effects.append(normalized)


def waive_effect(owner: Any, effect: str, *, evidence_ref: int | str | None, allow_legacy: bool) -> None:
    normalized = _normalize_effect(effect)
    match = next(
        (item for item in owner._obligations if item.kind == "effect" and item.effect == normalized),
        None,
    )
    if match is None:
        if normalized not in owner.requested_effects:
            raise TaskSemanticsError("efeito nao solicitado")
        if normalized not in owner._waived_effects:
            owner._waived_effects.append(normalized)
        return
    transition(
        owner,
        match.id,
        ObligationStatus.WAIVED,
        evidence_ref=evidence_ref,
        allow_legacy=allow_legacy,
    )
    if normalized not in owner._waived_effects:
        owner._waived_effects.append(normalized)


def register_observation(
    owner: Any,
    tool_name: str,
    result: Mapping[str, Any],
    evidence_ref: int | str,
    *,
    args: Mapping[str, Any] | None = None,
) -> None:
    ref = _eligible_evidence_ref(evidence_ref)
    if not isinstance(result, Mapping):
        raise TaskSemanticsError("observacao canonica invalida")
    previous = getattr(owner, "_evidence_catalog", {}).get(ref)
    observation = {
        "tool": str(tool_name).strip().casefold(),
        "args": dict(args) if isinstance(args, Mapping) else {},
        "result": dict(result),
    }
    if previous is not None and previous.get("tool") and previous != observation:
        raise TaskSemanticsError("referencia de evidencia reutilizada para observacoes distintas")
    owner._evidence_catalog[ref] = observation


def observe_tool(
    owner: Any,
    tool_name: str,
    result: Mapping[str, Any],
    evidence_ref: int | str,
    *,
    args: Mapping[str, Any] | None = None,
) -> tuple[str, ...]:
    register_observation(owner, tool_name, result, evidence_ref, args=args)
    if not result_is_successful(result):
        ref = _eligible_evidence_ref(evidence_ref)
        satisfied_failures: list[str] = []
        if classify_failure(result) is not FailureClass.LOCAL:
            return ()
        for item in owner.pending_obligations():
            if item.kind == "fallback" and matches_fallback(item, tool_name, result, args):
                satisfy(owner, item.id, ref)
                satisfied_failures.append(item.id)
                for required_read in owner.pending_obligations():
                    if (
                        required_read.kind == "read"
                        and same_identity(required_read.target, item.fallback_target)
                    ):
                        waive(owner, required_read.id, ref)
                        satisfied_failures.append(required_read.id)
        return tuple(satisfied_failures)
    ref = _eligible_evidence_ref(evidence_ref)
    tool = str(tool_name).strip().casefold()
    satisfied: list[str] = []
    for item in owner.pending_obligations():
        if item.kind != "effect" and matches_requirement(owner, item, tool, result, args):
            satisfy(owner, item.id, ref)
            satisfied.append(item.id)
    satisfied.extend(_satisfy_comparisons_from_reads(owner))
    return tuple(satisfied)


def _satisfy_comparisons_from_reads(owner: Any) -> list[str]:
    reads: dict[str, tuple[int | str, Mapping[str, Any]]] = {}
    for ref, item in getattr(owner, "_evidence_catalog", {}).items():
        if item.get("tool") not in _READ_TOOLS:
            continue
        result = item.get("result")
        if not isinstance(result, Mapping) or not complete_observation(result):
            continue
        path = arg_path(item.get("args"))
        if path is not None:
            reads[path.casefold()] = (ref, result)
    satisfied: list[str] = []
    for item in owner.pending_obligations():
        if item.kind != "compare" or len(item.operands) != 2:
            continue
        left = reads.get(item.operands[0].casefold())
        right = reads.get(item.operands[1].casefold())
        if left is None or right is None:
            continue
        satisfy(owner, item.id, evidence_ref=left[0])
        transition(owner, item.id, ObligationStatus.SATISFIED, evidence_ref=right[0])
        satisfied.append(item.id)
    return satisfied


def replace_effects(owner: Any, requested_effects: Sequence[str], prohibited_effects: Sequence[str]) -> None:
    requested = tuple(dict.fromkeys(_normalize_effect(item) for item in requested_effects))
    prohibited = tuple(dict.fromkeys(_normalize_effect(item) for item in prohibited_effects))
    non_effect = tuple(item for item in owner._obligations if item.kind != "effect")
    owner._intent = TaskIntent(owner.objective, requested, prohibited)
    owner._obligations = non_effect + tuple(
        TaskObligation(
            id=f"effect:{effect}",
            kind="effect",
            effect=effect,
            description=f"Produzir o efeito operacional solicitado: {effect}.",
        )
        for effect in requested
    )
    previous = owner._statuses
    previous_evidence = owner._evidence
    owner._statuses = {item.id: previous.get(item.id, ObligationStatus.PENDING) for item in owner._obligations}
    owner._evidence = {item.id: list(previous_evidence.get(item.id, ())) for item in owner._obligations}


def reset_progress(owner: Any) -> None:
    owner._statuses = {item.id: ObligationStatus.PENDING for item in owner._obligations}
    owner._evidence = {item.id: [] for item in owner._obligations}
    owner._executed_effects = []
    owner._waived_effects = []
    owner._evidence_catalog = {}

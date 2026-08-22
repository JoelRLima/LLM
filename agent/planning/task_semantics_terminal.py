"""Aggregate provenance rules for terminal task-obligation transitions."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from agent.planning.failure_policy import FailureClass, classify_failure
from agent.planning.task_semantics_effects import effect_observation_proves_terminal
from agent.planning.task_semantics_evidence import (
    _READ_TOOLS,
    arg_path,
    compare_args_match,
    complete_observation,
    matches_fallback,
    matches_requirement,
    same_identity,
)
from agent.planning.task_semantics_types import (
    ObligationStatus,
    TaskSemanticsError,
    _eligible_evidence_ref,
)


def validate_terminal_evidence(
    owner: Any,
    obligation_id: str,
    status: ObligationStatus,
    evidence_refs: Sequence[int | str],
    *,
    effect_authority: Any = None,
) -> None:
    """Validate the complete provenance set for one terminal transition."""

    if status is ObligationStatus.PENDING:
        raise TaskSemanticsError("status pending nao e terminal")
    refs = tuple(_eligible_evidence_ref(ref) for ref in evidence_refs)
    if not refs:
        raise TaskSemanticsError("obrigacao terminal restaurada sem evidencia")
    if len(set(refs)) != len(refs):
        raise TaskSemanticsError("evidencia terminal contem referencias duplicadas")
    obligation = next(
        (item for item in owner._obligations if item.id == obligation_id),
        None,
    )
    if obligation is None:
        raise TaskSemanticsError("obrigacao desconhecida")
    if obligation.kind == "effect" and any(
        isinstance(ref, str) and ref.startswith("legacy:")
        for ref in refs
    ):
        raise TaskSemanticsError("evidencia sintetica nao pode provar efeito operacional")
    observations = _catalog_observations(owner, refs)
    if obligation.kind == "effect":
        _validate_effect_observations(
            effect_authority,
            status,
            observations,
        )
        return
    if not _evidence_set_proves_requirement(owner, obligation, status, refs, observations):
        raise TaskSemanticsError("evidencia nao prova a obrigacao especifica")


def _catalog_observations(
    owner: Any,
    refs: Sequence[int | str],
) -> tuple[Mapping[str, Any], ...]:
    catalog = getattr(owner, "_evidence_catalog", {})
    observations: list[Mapping[str, Any]] = []
    for ref in refs:
        observation = catalog.get(ref)
        if (
            not isinstance(observation, Mapping)
            or not str(observation.get("tool") or "").strip()
            or not isinstance(observation.get("result"), Mapping)
        ):
            raise TaskSemanticsError(
                "evidencia terminal restaurada nao pertence ao historico canonico"
            )
        observations.append(observation)
    return tuple(observations)


def _validate_effect_observations(
    authority: Any,
    status: ObligationStatus,
    observations: Sequence[Mapping[str, Any]],
) -> None:
    if authority is None or not all(
        effect_observation_proves_terminal(authority, status, observation)
        for observation in observations
    ):
        raise TaskSemanticsError("evidencia nao prova o efeito operacional")


def _evidence_set_proves_requirement(
    owner: Any,
    obligation: Any,
    status: ObligationStatus,
    refs: Sequence[int | str],
    observations: Sequence[Mapping[str, Any]],
) -> bool:
    if status is ObligationStatus.SATISFIED and obligation.kind == "compare":
        return _compare_evidence_set_proves(obligation, observations)
    if status is ObligationStatus.WAIVED:
        return all(
            _evidence_proves_waiver(owner, obligation, observation, ref)
            for ref, observation in zip(refs, observations, strict=True)
        )
    if status is ObligationStatus.BLOCKED:
        return _blocked_evidence_set_proves(obligation, observations)
    return all(
        _evidence_proves_requirement(owner, obligation, observation, ref)
        for ref, observation in zip(refs, observations, strict=True)
    )


def _observation_parts(
    observation: Mapping[str, Any],
) -> tuple[str, Mapping[str, Any], Mapping[str, Any] | None]:
    result = observation["result"]
    args = observation.get("args") if isinstance(observation.get("args"), Mapping) else None
    return str(observation.get("tool", "")), result, args


def _compare_evidence_set_proves(
    obligation: Any,
    observations: Sequence[Mapping[str, Any]],
) -> bool:
    if all(
        matches_requirement(
            None,
            obligation,
            tool,
            result,
            args,
        )
        for tool, result, args in map(_observation_parts, observations)
    ):
        return True
    covered: set[int] = set()
    for tool, result, args in map(_observation_parts, observations):
        if tool not in _READ_TOOLS or not complete_observation(result):
            return False
        for index, operand in enumerate(obligation.operands):
            if same_identity(operand, arg_path(args)):
                covered.add(index)
                break
    return covered == {0, 1}


def _evidence_proves_waiver(
    owner: Any,
    obligation: Any,
    observation: Mapping[str, Any],
    evidence_ref: int | str,
) -> bool:
    _tool, result, args = _observation_parts(observation)
    if obligation.kind != "read" or classify_failure(result) is not FailureClass.LOCAL:
        return False
    return any(
        item.kind == "fallback"
        and owner._statuses[item.id] is ObligationStatus.SATISFIED
        and evidence_ref in owner._evidence[item.id]
        and same_identity(item.fallback_target, arg_path(args))
        for item in owner._obligations
    )


def _blocked_evidence_set_proves(
    obligation: Any,
    observations: Sequence[Mapping[str, Any]],
) -> bool:
    if obligation.kind == "compare":
        direct = all(
            classify_failure(result) is not FailureClass.NONE
            and tool in {"compare", "diff", "code_analyzer"}
            and compare_args_match(obligation.operands, args)
            for tool, result, args in map(_observation_parts, observations)
        )
        if direct:
            return True
        covered: set[int] = set()
        for tool, result, args in map(_observation_parts, observations):
            if classify_failure(result) is FailureClass.NONE or tool not in _READ_TOOLS:
                return False
            for index, operand in enumerate(obligation.operands):
                if same_identity(operand, arg_path(args)):
                    covered.add(index)
                    break
        return covered == {0, 1}
    return all(
        classify_failure(_observation_parts(observation)[1]) is not FailureClass.NONE
        and _failure_matches_obligation(obligation, observation)
        for observation in observations
    )


def _failure_matches_obligation(obligation: Any, observation: Mapping[str, Any]) -> bool:
    tool, result, args = _observation_parts(observation)
    if obligation.kind == "read":
        return tool in _READ_TOOLS and same_identity(obligation.target, arg_path(args))
    if obligation.kind == "search":
        return tool in {"grep", "search"} and (
            same_identity(obligation.query, args.get("pattern") if args else None)
            or same_identity(obligation.query, args.get("query") if args else None)
        )
    if obligation.kind == "analyze":
        return tool in {"code_analyzer", "analyze"} and (
            same_identity(obligation.target, arg_path(args))
            or same_identity(obligation.query, args.get("query") if args else None)
        )
    return obligation.kind == "fallback" and matches_fallback(obligation, tool, result, args)


def _evidence_proves_requirement(
    owner: Any,
    obligation: Any,
    observation: Mapping[str, Any],
    evidence_ref: int | str,
) -> bool:
    tool, result, args = _observation_parts(observation)
    if (
        obligation.kind == "read"
        and classify_failure(result) is FailureClass.LOCAL
        and any(
            item.kind == "fallback"
            and owner._statuses[item.id] is ObligationStatus.SATISFIED
            and evidence_ref in owner._evidence[item.id]
            and same_identity(item.fallback_target, arg_path(args))
            for item in owner._obligations
        )
    ):
        return True
    if matches_requirement(owner, obligation, tool, result, args, evidence_ref=evidence_ref):
        return True
    return obligation.kind == "fallback" and matches_fallback(obligation, tool, result, args)


__all__ = ["validate_terminal_evidence"]

"""Canonical durable task intent, obligations, and effect semantics."""

from __future__ import annotations

import posixpath
from dataclasses import replace
from typing import Any, Mapping, Sequence, cast

from agent.planning.task_semantics_admission_api import TaskSemanticsAdmissionMixin
from agent.planning.task_semantics_authority import (
    AuthorityConstraint,
    AuthorityDecision,
    AuthorizedEffect,
    EffectAuthority,
    EffectAuthorityDecision,
    PositiveAuthorityProof,
    admit_effect_authority,
)
from agent.planning.task_semantics_checkpoint import restore_from_checkpoint, snapshot, to_checkpoint_dict
from agent.planning.task_semantics_effect_transitions import (
    record_effect,
    record_prohibited_effect,
    record_unrequested_effect,
    waive_effect,
)
from agent.planning.task_semantics_inference import (
    infer_effect_semantics,
    inferred_obligations,
    predicate_evidence_from_observation,
)
from agent.planning.task_semantics_lifecycle import replace_effects, reset_progress
from agent.planning.task_semantics_projection import TaskSemanticsProjectionMixin
from agent.planning.task_semantics_review import (
    ObligationRejection,
    ObligationReviewResult,
)
from agent.planning.task_semantics_storage import initialize_semantics
from agent.planning.task_semantics_transitions import (
    block,
    observe_tool,
    register_observation,
    satisfy,
    waive,
)
from agent.planning.task_semantics_types import (
    MAX_OBLIGATIONS,
    MAX_REVIEW_OBLIGATIONS,
    OBLIGATION_KINDS,
    AdmissionSource,
    EffectIntent,
    EffectSemantics,
    ObligationStatus,
    PredicateEvidence,
    PredicateResolutionState,
    TaskIntent,
    TaskObligation,
    TaskSemanticsError,
    _normalize_id,
    _normalize_predicate_id,
    _normalize_text,
    validate_closed_obligation,
)


def infer_requested_effects(objective: str) -> tuple[str, ...]:
    """Compatibility projection of the canonical admitted effect authority."""

    return admit_effect_authority(objective).requested_effects


def infer_prohibited_effects(objective: str) -> tuple[str, ...]:
    """Compatibility projection of canonical denied effect candidates."""

    authority = admit_effect_authority(objective)
    return tuple(dict.fromkeys(item.effect for item in authority.constraint_intents))


class TaskSemantics(TaskSemanticsProjectionMixin, TaskSemanticsAdmissionMixin):
    """Single mutable owner for durable task requirements and their evidence."""

    _intent: TaskIntent
    _obligations: tuple[TaskObligation, ...]
    _statuses: dict[str, ObligationStatus]
    _evidence: dict[str, list[int | str]]
    _status_claims: dict[str, ObligationStatus]
    _evidence_claims: dict[str, list[int | str]]
    _evidence_catalog: dict[int | str, dict[str, Any]]
    _executed_effects: list[str]
    _waived_effects: list[str]
    _unrequested_effects: list[str]
    _prohibited_effects_occurred: list[str]
    _predicate_resolutions: dict[str, PredicateEvidence]
    _strict_evidence: bool
    _effect_authority: EffectAuthority | None
    _candidate_effect_intents: tuple[EffectIntent, ...]
    _authority_mode: str

    def __init__(
        self,
        intent: TaskIntent,
        obligations: Sequence[TaskObligation] = (),
        *,
        statuses: Mapping[str, str | ObligationStatus] | None = None,
        evidence: Mapping[str, Sequence[int | str]] | None = None,
        executed_effects: Sequence[str] = (),
        waived_effects: Sequence[str] = (),
        predicate_resolutions: Mapping[str, PredicateEvidence | Mapping[str, Any]] | None = None,
        effect_authority: EffectAuthority | None = None,
        candidate_effect_intents: Sequence[EffectIntent] | None = None,
        _strict_evidence: bool = False,
    ) -> None:
        self._strict_evidence = _strict_evidence
        if effect_authority is not None and not isinstance(effect_authority, EffectAuthority):
            raise TaskSemanticsError("autoridade de efeito invalida")
        self._effect_authority = effect_authority
        self._candidate_effect_intents = tuple(candidate_effect_intents or ())
        if any(not isinstance(item, EffectIntent) for item in self._candidate_effect_intents):
            raise TaskSemanticsError("candidatos de efeito invalidos")
        self._authority_mode = (
            "objective_positive"
            if effect_authority is not None
            else "structured"
            if not intent.original_objective.strip() or intent.requested_effects
            else "objective"
        )
        self._unrequested_effects = []
        self._prohibited_effects_occurred = []
        self._predicate_resolutions = {}
        self._pending_predicate_resolutions: dict[str, PredicateEvidence] = {}
        self._evidence_catalog = {}
        initialize_semantics(
            self,
            intent,
            obligations,
            statuses=statuses,
            evidence=evidence,
            executed_effects=executed_effects,
            waived_effects=waived_effects,
        )
        self._restore_predicate_resolutions(predicate_resolutions)

    @classmethod
    def empty(cls, objective: str = "") -> "TaskSemantics":
        return cls(TaskIntent(str(objective or "")), _strict_evidence=True)

    @classmethod
    def from_objective(cls, objective: str) -> "TaskSemantics":
        candidates = infer_effect_semantics(objective)
        authority = admit_effect_authority(objective, candidates)
        admitted_intents = authority.admitted_intents
        admitted_requested = authority.requested_effects
        admitted_prohibited = tuple(
            dict.fromkeys(item.effect for item in authority.constraint_intents)
        )
        admitted_effects = EffectSemantics(
            requested=admitted_requested,
            prohibited=admitted_prohibited,
            intents=admitted_intents,
            proposal_only=candidates.proposal_only,
        )
        return cls(
            TaskIntent(
                objective,
                admitted_requested,
                admitted_prohibited,
                effect_intents=admitted_intents,
            ),
            inferred_obligations(objective, admitted_effects, authority=authority),
            effect_authority=authority,
            candidate_effect_intents=candidates.intents,
            _strict_evidence=True,
        )

    @classmethod
    def from_legacy(
        cls,
        objective: str,
        requested_effects: Sequence[str],
        executed_effects: Sequence[str] = (),
        waived_effects: Sequence[str] = (),
        prohibited_effects: Sequence[str] = (),
    ) -> "TaskSemantics":
        base = (
            cls.from_objective(objective)
            if objective
            else cls(TaskIntent(""), _strict_evidence=True)
        )
        # A legacy checkpoint is a compatibility projection, not a fresh
        # objective-derived authority decision.  Preserve its effect-shaped
        # obligations for replay/reporting, while marking the owner so
        # objective admission cannot use the legacy list as a new semantic
        # source.
        # The legacy path must not retain an objective authority ledger while
        # accepting its serialized effect projection.  Its obligations remain
        # replayable, but live execution still requires the separate
        # operational evidence/authority gate.
        base._effect_authority = None
        base._authority_mode = "legacy"
        base.replace_effects(requested_effects, prohibited_effects)
        # Legacy effect lists are claims, not operational evidence.  They are
        # intentionally ignored here; a restore may rebuild them only from the
        # canonical observation history and live effect authority.
        del executed_effects, waived_effects
        return base

    def satisfy(self, obligation_id: str, *, evidence_ref: int | str, effect_authority: Any = None) -> None:
        satisfy(self, _normalize_id(obligation_id), evidence_ref, effect_authority=effect_authority)

    def waive(self, obligation_id: str, *, evidence_ref: int | str, effect_authority: Any = None) -> None:
        waive(self, _normalize_id(obligation_id), evidence_ref, effect_authority=effect_authority)

    def block(self, obligation_id: str, *, evidence_ref: int | str, effect_authority: Any = None) -> None:
        block(self, _normalize_id(obligation_id), evidence_ref, effect_authority=effect_authority)

    def record_effect(self, effect: str, *, evidence_ref: int | str | None = None, allow_legacy: bool = False, effect_authority: Any = None) -> None:
        record_effect(self, effect, evidence_ref=evidence_ref, allow_legacy=allow_legacy, effect_authority=effect_authority)

    def waive_effect(self, effect: str, *, evidence_ref: int | str | None = None, allow_legacy: bool = False, effect_authority: Any = None) -> None:
        waive_effect(self, effect, evidence_ref=evidence_ref, allow_legacy=allow_legacy, effect_authority=effect_authority)

    def record_unrequested_effect(
        self,
        effect: str,
        *,
        evidence_ref: int | str,
        effect_authority: Any,
        force: bool = False,
    ) -> None:
        record_unrequested_effect(
            self,
            effect,
            evidence_ref=evidence_ref,
            effect_authority=effect_authority,
            force=force,
        )

    def record_prohibited_effect(
        self,
        effect: str,
        *,
        evidence_ref: int | str,
        effect_authority: Any,
    ) -> None:
        record_prohibited_effect(
            self,
            effect,
            evidence_ref=evidence_ref,
            effect_authority=effect_authority,
        )

    def observe_tool(
        self,
        tool_name: str,
        result: Mapping[str, Any],
        *,
        evidence_ref: int | str,
        args: Mapping[str, Any] | None = None,
    ) -> tuple[str, ...]:
        satisfied = observe_tool(self, tool_name, result, evidence_ref, args=args)
        self._resolve_predicates_from_observation(evidence_ref)
        return satisfied

    def register_observation(
        self,
        tool_name: str,
        result: Mapping[str, Any],
        *,
        evidence_ref: int | str,
        args: Mapping[str, Any] | None = None,
    ) -> None:
        register_observation(self, tool_name, result, evidence_ref, args=args)

    def replace_effects(self, requested_effects: Sequence[str], prohibited_effects: Sequence[str] = ()) -> None:
        replace_effects(self, requested_effects, prohibited_effects)

    def reset_progress(self) -> None: reset_progress(self)
    def snapshot(self) -> tuple[dict[str, Any], ...]:
        return snapshot(self)

    def to_checkpoint_dict(self) -> dict[str, Any]:
        return to_checkpoint_dict(self)

    @classmethod
    def from_checkpoint_dict(cls, data: Mapping[str, Any]) -> "TaskSemantics":
        return cast("TaskSemantics", restore_from_checkpoint(cls, data))

    @property
    def predicate_resolutions(self) -> Mapping[str, PredicateEvidence]:
        """Read-only trusted predicate evidence keyed by canonical identity."""

        return dict(self._predicate_resolutions)

    def predicate_resolution(self, predicate_id: str) -> PredicateEvidence | None:
        return self._predicate_resolutions.get(_normalize_predicate_id(predicate_id))

    def resolve_predicate(
        self,
        predicate_id: str,
        value: bool,
        *,
        evidence_ref: int | str,
        provenance: str,
    ) -> None:
        """Resolve a known predicate from trusted runtime evidence."""

        normalized_id = _normalize_predicate_id(predicate_id)
        if type(value) is not bool:
            raise TaskSemanticsError("valor de predicate deve ser booleano")
        if not any(item.predicate_id == normalized_id for item in self.effect_intents):
            raise TaskSemanticsError("predicate desconhecido para o objetivo")
        evidence = PredicateEvidence(
            normalized_id,
            PredicateResolutionState.TRUE if value else PredicateResolutionState.FALSE,
            evidence_ref,
            provenance,
        )
        self._pending_predicate_resolutions.pop(normalized_id, None)
        self._predicate_resolutions[normalized_id] = evidence
        self._apply_predicate_evidence(evidence)

    def invalidate_predicate(self, predicate_id: str) -> None:
        """Drop stale evidence so a restored/changed observation fails closed."""

        normalized_id = _normalize_predicate_id(predicate_id)
        self._predicate_resolutions.pop(normalized_id, None)
        self._pending_predicate_resolutions.pop(normalized_id, None)
        self._apply_predicate_evidence(None, predicate_id=normalized_id)

    def invalidate_predicates_for_targets(
        self, targets: Sequence[str]
    ) -> tuple[str, ...]:
        """Invalidate observation-backed predicates for attempted workspace targets.

        A proposed or unverified workspace mutation can make a prior read
        stale even when the transaction ultimately reports no surviving
        mutation.  The target match is lexical and uses the same bounded
        identity normalization as predicate inference; no model text is
        consulted.
        """

        normalized_targets = {
            posixpath.normpath(
                _normalize_text(str(target)).replace("\\", "/").strip("/")
            )
            for target in targets
            if isinstance(target, str) and target.strip()
        }
        if not normalized_targets:
            return ()
        predicate_ids = tuple(
            dict.fromkeys(
                item.predicate_id
                for item in self.effect_intents
                if item.predicate_id is not None
            )
        )
        invalidated: list[str] = []
        for predicate_id in predicate_ids:
            target = predicate_id.split("|", 1)[0]
            if target not in normalized_targets:
                continue
            self.invalidate_predicate(predicate_id)
            invalidated.append(predicate_id)
        return tuple(invalidated)

    def revalidate_predicate_resolutions(self) -> None:
        """Re-prove restored observation-backed predicate evidence.

        Checkpoint construction happens before the surrounding AgentState has
        registered its tool history.  Observation-backed resolutions therefore
        remain pending until this method can compare the original observation
        with the canonical predicate identity.  Missing, changed, or
        malformed evidence is discarded and leaves the branch unresolved.
        """

        pending = dict(self._pending_predicate_resolutions)
        self._pending_predicate_resolutions.clear()
        for predicate_id, evidence in pending.items():
            if evidence.provenance == "deterministic_fact":
                self._predicate_resolutions[predicate_id] = evidence
                self._apply_predicate_evidence(evidence)
                continue
            if not self._predicate_evidence_matches_history(evidence):
                self._predicate_resolutions.pop(predicate_id, None)
                self._apply_predicate_evidence(None, predicate_id=predicate_id)
                continue
            self._predicate_resolutions[predicate_id] = evidence
            self._apply_predicate_evidence(evidence)

    def _predicate_evidence_matches_history(self, evidence: PredicateEvidence) -> bool:
        observation = self._evidence_catalog.get(evidence.evidence_ref)
        admitted = predicate_evidence_from_observation(
            evidence.predicate_id,
            observation,
            evidence_ref=evidence.evidence_ref,
        ) if isinstance(observation, Mapping) else None
        return admitted is not None and admitted.value is evidence.value

    def _resolve_predicates_from_observation(self, evidence_ref: int | str) -> None:
        observation = self._evidence_catalog.get(evidence_ref)
        if not isinstance(observation, Mapping):
            return
        for predicate_id in tuple(
            dict.fromkeys(
                item.predicate_id
                for item in self.effect_intents
                if item.predicate_id is not None
            )
        ):
            if predicate_id is None:
                continue
            evidence = predicate_evidence_from_observation(
                predicate_id, observation, evidence_ref=evidence_ref
            )
            if evidence is not None:
                self.resolve_predicate(
                    predicate_id,
                    evidence.value,
                    evidence_ref=evidence.evidence_ref,
                    provenance=evidence.provenance,
                )

    def _restore_predicate_resolutions(
        self,
        raw: Mapping[str, PredicateEvidence | Mapping[str, Any]] | None,
    ) -> None:
        if raw is not None and not isinstance(raw, Mapping):
            raise TaskSemanticsError("resolucoes de predicate invalidas")
        known = {item.predicate_id for item in self.effect_intents if item.predicate_id is not None}
        for predicate_id, value in (raw or {}).items():
            if isinstance(value, PredicateEvidence):
                evidence = value
            elif isinstance(value, Mapping):
                try:
                    evidence = PredicateEvidence(
                        predicate_id,
                        value["state"],
                        value["evidence_ref"],
                        value["provenance"],
                    )
                except (KeyError, TypeError, ValueError) as exc:
                    raise TaskSemanticsError("resolucao de predicate invalida") from exc
            else:
                raise TaskSemanticsError("resolucao de predicate invalida")
            if evidence.predicate_id not in known:
                raise TaskSemanticsError("resolucao de predicate desconhecida")
            if evidence.provenance == "deterministic_fact" or evidence.evidence_ref in self._evidence_catalog:
                self._predicate_resolutions[evidence.predicate_id] = evidence
                self._apply_predicate_evidence(evidence)
            else:
                self._pending_predicate_resolutions[evidence.predicate_id] = evidence
                self._apply_predicate_evidence(None, predicate_id=evidence.predicate_id)

        # Intent-level evidence is also accepted when restoring a checkpoint
        # written by an older adapter that did not emit the top-level map.
        for intent in self.effect_intents:
            if intent.predicate_id is None or intent.predicate_state is PredicateResolutionState.UNRESOLVED:
                continue
            if intent.predicate_evidence_ref is None or intent.predicate_provenance is None:
                raise TaskSemanticsError("intent de predicate resolvido sem evidencia")
            evidence = PredicateEvidence(
                intent.predicate_id,
                intent.predicate_state,
                intent.predicate_evidence_ref,
                intent.predicate_provenance,
            )
            prior = self._predicate_resolutions.get(evidence.predicate_id)
            if prior is not None and prior != evidence:
                raise TaskSemanticsError("evidencias de predicate conflitantes")
            if evidence.provenance == "deterministic_fact" or evidence.evidence_ref in self._evidence_catalog:
                self._predicate_resolutions[evidence.predicate_id] = evidence
            else:
                self._pending_predicate_resolutions[evidence.predicate_id] = evidence
                self._apply_predicate_evidence(None, predicate_id=evidence.predicate_id)

    def _apply_predicate_evidence(
        self,
        evidence: PredicateEvidence | None,
        *,
        predicate_id: str | None = None,
    ) -> None:
        target_id = predicate_id or (evidence.predicate_id if evidence is not None else None)
        if target_id is None:
            raise TaskSemanticsError("predicate ausente")
        updated = []
        for intent in self.effect_intents:
            if intent.predicate_id != target_id:
                updated.append(intent)
                continue
            if evidence is None:
                updated.append(
                    replace(
                        intent,
                        predicate_state=PredicateResolutionState.UNRESOLVED,
                        predicate_evidence_ref=None,
                        predicate_provenance=None,
                    )
                )
            else:
                updated.append(
                    replace(
                        intent,
                        predicate_state=evidence.state,
                        predicate_evidence_ref=evidence.evidence_ref,
                        predicate_provenance=evidence.provenance,
                    )
                )
        self._intent = TaskIntent(
            self.objective,
            self.requested_effects,
            self.prohibited_effects,
            effect_intents=tuple(updated),
        )

__all__ = (
    "AuthorityDecision", "AuthorityConstraint",
    "AuthorizedEffect", "EffectAuthority",
    "EffectAuthorityDecision",
    "EffectSemantics",
    "admit_effect_authority",
    "AdmissionSource",
    "ObligationReviewResult",
    "ObligationRejection",
    "MAX_OBLIGATIONS",
    "MAX_REVIEW_OBLIGATIONS",
    "OBLIGATION_KINDS",
    "ObligationStatus",
    "PredicateEvidence",
    "PredicateResolutionState",
    "PositiveAuthorityProof",
    "TaskIntent",
    "TaskObligation",
    "TaskSemantics",
    "TaskSemanticsError",
    "validate_closed_obligation",
    "infer_effect_semantics",
    "infer_prohibited_effects",
    "infer_requested_effects",
)

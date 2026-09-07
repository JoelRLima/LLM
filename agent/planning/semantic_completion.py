"""Build the durable completion projection from admitted W14 intent."""

from __future__ import annotations

from typing import Any

from agent.planning.intent_admission import AdmittedIntent
from agent.planning.target_grounding import GroundedTargetSet
from agent.planning.task_semantics import TaskSemantics
from agent.planning.task_semantics_types import (
    EffectIntent,
    TaskIntent,
    TaskObligation,
    TaskSemanticsError,
)


def task_semantics_from_admitted_intent(
    objective: str,
    admitted_intent: Any,
    grounded_targets: Any,
) -> TaskSemantics:
    """Build completion semantics from trusted W14 admission facts."""

    if not isinstance(admitted_intent, AdmittedIntent):
        raise TaskSemanticsError("intent admitida invalida")
    if not isinstance(grounded_targets, GroundedTargetSet):
        raise TaskSemanticsError("alvos aterrados invalidos")

    grounded_by_selector = {
        item.selector_id: item.resource for item in grounded_targets.targets
    }
    selector_literals = {
        item.selector_id: item.literal_resource
        for item in admitted_intent.selectors
        if item.literal_resource is not None
    }

    def effect_targets(effect: Any) -> tuple[str, ...]:
        resources = tuple(
            dict.fromkeys(
                grounded_by_selector.get(selector_id)
                or selector_literals.get(selector_id)
                for selector_id in effect.selector_ids
            )
        )
        selected = tuple(item for item in resources if isinstance(item, str) and item)
        if not selected:
            raise TaskSemanticsError("efeito admitido sem alvo aterrado")
        return selected

    requested_effects = tuple(
        dict.fromkeys(item.effect for item in admitted_intent.requested_effects)
    )
    prohibited_effects = tuple(
        dict.fromkeys(item.effect for item in admitted_intent.prohibited_effects)
    )
    effect_intents: list[EffectIntent] = []
    for effect in (*admitted_intent.requested_effects, *admitted_intent.prohibited_effects):
        for target in effect_targets(effect):
            effect_intents.append(
                EffectIntent(
                    effect.effect,
                    target=target,
                    polarity="prohibited" if effect.prohibited else "requested",
                    source="semantic_intent",
                )
            )

    obligations = tuple(
        TaskObligation(
            id=f"effect:{effect}",
            kind="effect",
            effect=effect,
            description=f"Produzir o efeito operacional solicitado: {effect}.",
        )
        for effect in requested_effects
    )
    intent = TaskIntent(
        objective,
        requested_effects,
        prohibited_effects,
        effect_intents=tuple(effect_intents),
    )
    semantics = TaskSemantics(
        intent,
        obligations,
        candidate_effect_intents=tuple(effect_intents),
        _strict_evidence=True,
    )
    semantics.proposal_only = admitted_intent.proposal_only  # type: ignore[attr-defined]
    semantics.requires_validation = admitted_intent.requires_validation  # type: ignore[attr-defined]
    return semantics


def install_admitted_intent_semantics(
    orchestrator: Any,
    objective: str,
    admitted_intent: Any,
    grounded_targets: Any,
) -> None:
    """Install the trusted W14 semantic projection for completion checks."""

    state = orchestrator.agent_state
    semantics = task_semantics_from_admitted_intent(
        objective,
        admitted_intent,
        grounded_targets,
    )
    state.set_task_semantics(semantics)
    state.reset_task_progression(
        semantics.requested_effects,
        preserve_semantics=True,
    )

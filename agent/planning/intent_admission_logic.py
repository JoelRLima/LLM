from __future__ import annotations

import hashlib
import json
from typing import Mapping

from agent.capabilities import (
    WRITE_CAPABILITIES,
    Capability,
    canonical_capabilities,
    capability_values,
)
from agent.interaction.intent_claim import (
    ConstraintClaim,
    EffectClaim,
    EvidenceSpan,
    IntentClaimV1,
    TargetSelectorClaim,
)
from agent.resources.contracts import normalize_resource_id

from .intent_admission_model import (
    _CANONICAL_DURABLE_EFFECTS,
    _EFFECT_CAPABILITY,
    _OPERATION_CAPABILITIES,
    AdmittedEffect,
    AdmittedIntent,
    AdmittedSelector,
    AuthorityEnvelope,
    IntentAdmissionError,
)


def _claim_fingerprint(claim: IntentClaimV1) -> str:
    encoded = json.dumps(claim.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _selector_evidence(claim: IntentClaimV1, selector: TargetSelectorClaim) -> tuple[EvidenceSpan, ...]:
    by_id = {item.span_id: item for item in claim.evidence_spans}
    return tuple(by_id[item] for item in selector.evidence_span_ids)


def _literal_resource(selector: TargetSelectorClaim, claim: IntentClaimV1) -> str | None:
    if selector.kind not in {"path_literal", "resource"}:
        return None
    value = selector.value.replace("\\", "/").strip()
    if not value or value.startswith("/") or value.startswith("//"):
        raise IntentAdmissionError("INTENT_LITERAL_TARGET_INVALID")
    if len(value) >= 2 and value[1] == ":":
        raise IntentAdmissionError("INTENT_LITERAL_TARGET_INVALID")
    if any(part in {"", ".", ".."} for part in value.split("/")):
        raise IntentAdmissionError("INTENT_LITERAL_TARGET_INVALID")
    exact_evidence = value in {span.text for span in _selector_evidence(claim, selector)}
    if selector.kind == "path_literal" and not exact_evidence:
        raise IntentAdmissionError("INTENT_LITERAL_TARGET_NOT_EVIDENCED")
    if selector.kind == "resource":
        # ``resource`` is reserved for logical resources with a deterministic
        # owner.  A model-selected filesystem-looking value is never allowed
        # to enter the path grounding flow merely because it was labelled as
        # a logical resource.  ``memory`` is the only such resource currently
        # owned by this admission boundary.
        if normalize_resource_id(value) == "memory":
            return "memory"
        if not exact_evidence:
            raise IntentAdmissionError("INTENT_LITERAL_TARGET_NOT_EVIDENCED")
        raise IntentAdmissionError("INTENT_RESOURCE_UNSUPPORTED")
    return normalize_resource_id(value)


def _required_capabilities(claim: IntentClaimV1, effects: tuple[EffectClaim, ...]) -> frozenset[Capability]:
    required = set(_OPERATION_CAPABILITIES[claim.operation])
    requested_effects = {
        item.effect.casefold()
        for item in effects
        if item.polarity == "requested"
    }
    if claim.operation == "do" and requested_effects and requested_effects <= {"memory_write"}:
        # Logical session memory is its own domain; a researcher with MEMORY
        # need not receive filesystem READ merely to perform a memory write.
        required.discard(Capability.READ)
    for item in effects:
        effect = item.effect.casefold()
        if effect not in _EFFECT_CAPABILITY:
            raise IntentAdmissionError("INTENT_UNKNOWN_EFFECT")
        if item.polarity == "requested" and effect == "memory_write":
            required.add(Capability.MEMORY)
    return frozenset(required)


def _admitted_selectors(claim: IntentClaimV1) -> tuple[list[AdmittedSelector], dict[str, AdmittedSelector]]:
    selectors = [
        AdmittedSelector(
            selector.selector_id,
            selector.kind,
            selector.value,
            selector.role,
            literal_resource=_literal_resource(selector, claim),
            symbolic=selector.kind == "symbol",
        )
        for selector in claim.selectors
    ]
    return selectors, {item.selector_id: item for item in selectors}


def _validate_effect_header(effect: EffectClaim, envelope: AuthorityEnvelope) -> str:
    normalized = effect.effect.casefold()
    if normalized not in _CANONICAL_DURABLE_EFFECTS:
        raise IntentAdmissionError("INTENT_UNKNOWN_EFFECT")
    if effect.polarity == "requested" and normalized not in envelope.granted_effects:
        raise IntentAdmissionError("INTENT_EFFECT_NOT_GRANTED", normalized)
    if (
        effect.polarity == "requested"
        and normalized == "write"
        and not any(envelope.has_capability(item) for item in WRITE_CAPABILITIES)
    ):
        # The semantic effect does not select the exact invocation capability,
        # but it still needs a trusted write-class ceiling.  VCS_WRITE is a
        # valid ceiling; WRITE is not required at admission time.
        raise IntentAdmissionError("INTENT_CAPABILITY_DENIED", "missing capabilities: write")
    if effect.polarity == "requested" and normalized == "memory_write" and not envelope.has_capability(Capability.MEMORY):
        raise IntentAdmissionError("INTENT_CAPABILITY_DENIED", "missing capabilities: memory")
    if not effect.selector_ids:
        raise IntentAdmissionError("INTENT_TARGET_REQUIRED")
    return normalized


def _validate_effect_selector(
    effect: EffectClaim,
    normalized: str,
    selector: AdmittedSelector,
    envelope: AuthorityEnvelope,
) -> tuple[str | None, bool]:
    if selector.role == "unknown":
        raise IntentAdmissionError("INTENT_TARGET_ROLE_UNKNOWN")
    if effect.polarity == "requested" and normalized == "write" and selector.role not in {"mutation_target", "destination", "resource"}:
        raise IntentAdmissionError("INTENT_WRITE_TARGET_ROLE_INVALID")
    _reject_memory_as_file_write(effect, normalized, selector)
    if effect.polarity == "requested" and normalized == "memory_write" and selector.role != "memory":
        raise IntentAdmissionError("INTENT_MEMORY_TARGET_ROLE_INVALID")
    if effect.polarity == "requested" and normalized == "memory_write":
        if selector.kind != "resource" or selector.literal_resource != "memory":
            raise IntentAdmissionError("INTENT_MEMORY_TARGET_INVALID")
        if not envelope.allows_write("memory"):
            raise IntentAdmissionError("INTENT_MEMORY_WRITE_SCOPE_DENIED")
    if selector.literal_resource is not None and effect.polarity == "requested" and normalized == "write":
        if not envelope.allows_read(selector.literal_resource):
            raise IntentAdmissionError("INTENT_READ_SCOPE_DENIED")
        if not envelope.allows_write(selector.literal_resource):
            raise IntentAdmissionError("INTENT_WRITE_SCOPE_DENIED")
    return selector.literal_resource, selector.symbolic


def _reject_memory_as_file_write(
    effect: EffectClaim,
    normalized: str,
    selector: AdmittedSelector,
) -> None:
    if (
        effect.polarity == "requested"
        and normalized == "write"
        and selector.literal_resource == "memory"
    ):
        raise IntentAdmissionError("INTENT_MEMORY_TARGET_INVALID")


def _admit_effect(
    effect: EffectClaim,
    by_id: dict[str, AdmittedSelector],
    envelope: AuthorityEnvelope,
) -> tuple[AdmittedEffect, set[str], bool]:
    normalized = _validate_effect_header(effect, envelope)
    literal_targets: set[str] = set()
    requires_grounding = False
    for selector_id in effect.selector_ids:
        literal, symbolic = _validate_effect_selector(
            effect,
            normalized,
            by_id[selector_id],
            envelope,
        )
        if literal is not None:
            literal_targets.add(literal)
        requires_grounding = requires_grounding or symbolic
    return (
        AdmittedEffect(normalized, effect.selector_ids, prohibited=effect.polarity == "prohibited"),
        literal_targets,
        requires_grounding,
    )


def _admit_effects(
    effects: tuple[EffectClaim, ...],
    by_id: dict[str, AdmittedSelector],
    envelope: AuthorityEnvelope,
) -> tuple[list[AdmittedEffect], list[AdmittedEffect], set[str], bool]:
    requested: list[AdmittedEffect] = []
    prohibited: list[AdmittedEffect] = []
    literal_targets: set[str] = set()
    requires_grounding = False
    for effect in effects:
        admitted, targets, grounding = _admit_effect(effect, by_id, envelope)
        (prohibited if admitted.prohibited else requested).append(admitted)
        # Only requested durable effects can establish mutation targets.  A
        # prohibited effect is a constraint, not a permission, even when its
        # selector carries a literal resource.
        if not admitted.prohibited:
            literal_targets.update(targets)
        requires_grounding = requires_grounding or grounding
    return requested, prohibited, literal_targets, requires_grounding


def _admit_constraints(
    constraints: tuple[ConstraintClaim, ...],
    trusted_predicates: Mapping[str, bool] | None,
    requested: list[AdmittedEffect],
) -> tuple[bool, bool, set[str]]:
    proposal_only = False
    requires_validation = False
    prohibited_constraint_effects: set[str] = set()
    for constraint in constraints:
        if constraint.kind == "proposal_only":
            proposal_only = True
        elif constraint.kind == "require_validation":
            requires_validation = True
        elif constraint.kind == "conditional":
            # The current wire shape has no expected state and no bounded
            # effect/selector binding.  Do not advertise a conditional DSL.
            raise IntentAdmissionError("INTENT_CONSTRAINT_UNSUPPORTED")
        elif constraint.kind == "prohibit_effect":
            # Prohibition is represented by a target-aware prohibited
            # EffectClaim.  This redundant constraint cannot be admitted
            # without a downstream representation, so fail closed.
            raise IntentAdmissionError("INTENT_CONSTRAINT_UNSUPPORTED")
        elif constraint.kind == "preserve":
            # No bounded preserve form is currently owned by a deterministic
            # executor.  Admission must not silently evaporate the claim.
            raise IntentAdmissionError("INTENT_CONSTRAINT_UNSUPPORTED")
        else:
            raise IntentAdmissionError("INTENT_CONSTRAINT_UNSUPPORTED")
    if any(item.effect in prohibited_constraint_effects for item in requested):
        raise IntentAdmissionError("INTENT_CONFLICT")
    return proposal_only, requires_validation, prohibited_constraint_effects


def admit_bound_intent(
    claim: IntentClaimV1,
    envelope: AuthorityEnvelope,
    trusted_predicates: Mapping[str, bool] | None,
) -> AdmittedIntent:
    """Build the trusted projection after the outer wrapper bound evidence."""

    if claim.ambiguity != "none":
        raise IntentAdmissionError("INTENT_AMBIGUOUS")
    required = _required_capabilities(claim, claim.effects)
    missing = required - canonical_capabilities(envelope.parent_permissions)
    if missing:
        raise IntentAdmissionError(
            "INTENT_CAPABILITY_DENIED",
            "missing capabilities: " + ", ".join(sorted(item.value for item in missing)),
        )
    selectors, by_id = _admitted_selectors(claim)
    requested, prohibited, literal_targets, requires_grounding = _admit_effects(
        claim.effects,
        by_id,
        envelope,
    )
    if {(item.effect, item.selector_ids) for item in requested} & {(item.effect, item.selector_ids) for item in prohibited}:
        raise IntentAdmissionError("INTENT_CONFLICT")
    proposal_only, requires_validation, _ = _admit_constraints(
        claim.constraints,
        trusted_predicates,
        requested,
    )
    # A semantic write is an effect ceiling, not an exact filesystem/VCS
    # operation, so it must not fabricate WRITE in this projection.
    selected_capabilities = set(required)
    if requires_validation:
        selected_capabilities.add(Capability.VALIDATE)
        if not envelope.has_capability(Capability.VALIDATE):
            raise IntentAdmissionError("INTENT_VALIDATION_CAPABILITY_DENIED")
    granted_projection = canonical_capabilities(envelope.parent_permissions)
    if not selected_capabilities.issubset(granted_projection):
        raise IntentAdmissionError("INTENT_CAPABILITY_PROJECTION_INVALID")
    return AdmittedIntent(
        operation=claim.operation,
        capabilities=capability_values(selected_capabilities),
        requested_effects=tuple(requested),
        prohibited_effects=tuple(prohibited),
        selectors=tuple(selectors),
        constraints=claim.constraints,
        evidence_spans=claim.evidence_spans,
        proposal_only=proposal_only or claim.operation == "plan",
        approval_required=envelope.approval_required,
        requires_grounding=requires_grounding,
        requires_validation=requires_validation,
        admitted_literal_targets=tuple(sorted(literal_targets)),
        authority_identity=envelope.authority_identity,
        claim_fingerprint=_claim_fingerprint(claim),
    )

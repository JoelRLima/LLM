"""Deterministic admission of an untrusted semantic intent claim.

This module is the W14 trust split in executable form.  ``IntentClaimV1`` is
model data; ``AuthorityEnvelope`` is a read-only projection of trusted runtime
state; ``AdmittedIntent`` is the only representation that downstream
authority-sensitive code should consume.
"""

from __future__ import annotations

from typing import Any, Mapping

from agent.capabilities import (
    WRITE_CAPABILITIES,
    Capability,
    canonical_capabilities,
    capability_values,
)
from agent.interaction.intent_claim import (
    IntentClaimError,
    IntentClaimV1,
    bind_current_subject_evidence,
)
from agent.planning.intent_admission_model import (
    _EFFECT_CAPABILITY,
    AdmittedEffect,
    AdmittedIntent,
    AdmittedSelector,
    AuthorityEnvelope,
    IntentAdmissionError,
)
from agent.resources.contracts import (
    WORKSPACE_RESOURCE,
    ResourceAccess,
    ResourceMode,
    ResourceProvenance,
)

from .memory_authority import add_trusted_memory_scopes


def authority_envelope_from_orchestrator(orchestrator: Any) -> AuthorityEnvelope:
    """Project the active trusted runtime ceiling into W14 authority facts.

    The projection is intentionally made after persona/directive capability
    refresh.  It does not inspect model output, plan metadata, or requested
    targets.  The workspace resource is the already-existing application
    boundary; symbolic discovery still has to resolve a unique concrete
    target and pass the independent write-scope check below.
    """

    permissions = canonical_capabilities(
        getattr(orchestrator, "allowed_capabilities", ())
    )
    explicit_read_resources = getattr(orchestrator, "read_resources", None)
    explicit_write_resources = getattr(orchestrator, "write_resources", None)
    read_resources: tuple[ResourceAccess, ...] = tuple(explicit_read_resources or ())
    write_resources: tuple[ResourceAccess, ...] = tuple(explicit_write_resources or ())
    if explicit_read_resources is None and Capability.READ in permissions:
        read_resources = (
            ResourceAccess(
                WORKSPACE_RESOURCE,
                ResourceMode.READ,
                ResourceProvenance.TRUSTED_DERIVED,
            ),
        )
    if explicit_write_resources is None and bool(WRITE_CAPABILITIES & permissions):
        write_resources = (
            ResourceAccess(
                WORKSPACE_RESOURCE,
                ResourceMode.WRITE,
                ResourceProvenance.TRUSTED_DERIVED,
            ),
        )
    read_resources, write_resources = add_trusted_memory_scopes(
        orchestrator,
        read_resources,
        write_resources,
        permissions,
    )
    task_authority = getattr(orchestrator, "task_authority", None)
    application_authority = getattr(orchestrator, "application_authority", None)
    identity = getattr(task_authority, "snapshot_id", None) or getattr(
        application_authority, "snapshot_id", None
    ) or "orchestrator-runtime"
    return AuthorityEnvelope(
        parent_permissions=capability_values(permissions),
        granted_effects=frozenset(
            effect
            for effect, capabilities in _EFFECT_CAPABILITY.items()
            if (
                bool(WRITE_CAPABILITIES & permissions)
                if effect == "write"
                else Capability.MEMORY in permissions
                and any(
                    getattr(item, "name", item) == "memory"
                    for item in write_resources
                )
            )
        ),
        read_resources=read_resources,
        write_resources=write_resources,
        workspace_root=getattr(orchestrator, "workspace_root", None),
        authority_identity=str(identity),
    )


def admit_intent_claim(
    claim: IntentClaimV1,
    envelope: AuthorityEnvelope,
    *,
    current_subject: str,
    trusted_predicates: Mapping[str, bool] | None = None,
) -> AdmittedIntent:
    """Bind evidence, then project trusted ``parent_permissions`` and ``allows_write`` facts."""

    if not isinstance(claim, IntentClaimV1):
        raise IntentAdmissionError("INTENT_CLAIM_INVALID")
    if not isinstance(envelope, AuthorityEnvelope):
        raise IntentAdmissionError("AUTHORITY_ENVELOPE_INVALID")
    try:
        bind_current_subject_evidence(claim, current_subject)
    except IntentClaimError as exc:
        raise IntentAdmissionError("INTENT_EVIDENCE_MISMATCH") from exc
    from .intent_admission_logic import admit_bound_intent

    return admit_bound_intent(claim, envelope, trusted_predicates)


__all__ = [
    "AdmittedEffect",
    "AdmittedIntent",
    "AdmittedSelector",
    "AuthorityEnvelope",
    "authority_envelope_from_orchestrator",
    "IntentAdmissionError",
    "admit_intent_claim",
]

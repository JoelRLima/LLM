"""Runtime-bound code outcome waiver predicates."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent.planning.task_semantics_effect_support import _invocation_semantics
from agent.reporting.observation_evidence import (
    result_executed,
    result_has_data,
    result_is_successful,
)
from agent.resources.contracts import (
    WORKSPACE_RESOURCE,
    ResourceAccess,
    ResourceMode,
    ResourceProvenance,
    normalize_resource_id,
    resources_overlap,
)
from agent.runtime.mutation_evidence import project_mutation_evidence
from agent.tools.invocation_semantics import CODE_WRITE_ACTIONS


def _code_outcome_parts(
    observation: Mapping[str, Any],
) -> tuple[Any, Any] | None:
    """Read the only accepted runtime seal location and its bound manifest."""

    result = observation.get("result")
    if not isinstance(result, Mapping):
        return None
    data = result.get("data")
    metadata = data.get("metadata") if isinstance(data, Mapping) else None
    if not isinstance(metadata, Mapping):
        return None
    seal_value = metadata.get("code_outcome")
    manifest_value = metadata.get("code_evidence_manifest")
    if not isinstance(seal_value, Mapping) or not isinstance(manifest_value, Mapping):
        return None
    try:
        from agent.code.outcome_verifier import CodeEvidenceManifest, CodeOutcomeSeal

        seal = CodeOutcomeSeal.from_dict(seal_value)
        manifest = CodeEvidenceManifest.from_dict(manifest_value)
    except (TypeError, ValueError, KeyError):
        return None
    if seal.evidence_manifest_id != manifest.manifest_id:
        return None
    if any(item not in manifest.by_id for item in seal.cited_evidence_ids):
        return None
    if not manifest.supports_no_change(
        seal.cited_evidence_ids,
        target_paths=seal.verified_resources,
    ):
        return None
    return seal, manifest


def code_outcome_seal_candidate(observation: Mapping[str, Any]) -> bool:
    """Return whether a structurally valid nested W13 seal is present."""

    return _code_outcome_parts(observation) is not None


def _workspace_root(authority: Any) -> Any | None:
    for name in ("workspace_root", "root"):
        value = getattr(authority, name, None)
        if value is not None:
            return value
    return None


def _write_code_semantics(authority: Any, observation: Mapping[str, Any]) -> Any | None:
    if not isinstance(observation, Mapping):
        return None
    result = observation.get("result")
    if not isinstance(result, Mapping):
        return None
    if not (
        result_executed(result) is True
        and result_is_successful(result)
        and result_has_data(result)
    ):
        return None
    mutation = project_mutation_evidence(result)
    if mutation.persisted_mutation or mutation.survives or mutation.occurred:
        return None
    if str(observation.get("tool") or "").strip().casefold() != "code_task":
        return None
    semantics = _invocation_semantics(authority, observation)
    if semantics is None or not _valid_write_semantics(semantics):
        return None
    accesses = tuple(semantics.resource_access)
    if any(
        access.mode is not ResourceMode.WRITE
        or access.name == WORKSPACE_RESOURCE
        for access in accesses
    ):
        return None
    return semantics


def _valid_write_semantics(semantics: Any) -> bool:
    return (
        semantics.action in CODE_WRITE_ACTIONS
        and "write" in semantics.durable_effects
        and not semantics.read_only
        and semantics.workspace_mutation
        and bool(semantics.resource_access)
    )


def _valid_seal_evidence(
    parts: tuple[Any, Any] | None,
    root: Any,
) -> tuple[Any, Any, tuple[Any, ...], tuple[str, ...]] | None:
    if parts is None or root is None:
        return None
    seal, manifest = parts
    if not manifest.validate() or not seal.evidence_current:
        return None
    leaves = manifest.required_file_content_ids(tuple(seal.cited_evidence_ids))
    if not leaves or not manifest.revalidate_files(root, leaves):
        return None
    leaf_records = tuple(manifest.by_id[item] for item in leaves)
    if any(
        record.kind != "FILE_CONTENT"
        or not record.complete
        or record.truncated
        or not record.text_lossless
        or not record.path
        or normalize_resource_id(record.path) == WORKSPACE_RESOURCE
        for record in leaf_records
    ):
        return None
    verified = tuple(normalize_resource_id(item) for item in seal.verified_resources)
    if not verified or any(item == WORKSPACE_RESOURCE for item in verified):
        return None
    if any(
        not any(
            record.path is not None
            and resources_overlap(normalize_resource_id(record.path), resource)
            for record in leaf_records
        )
        for resource in verified
    ):
        return None
    return seal, manifest, leaf_records, verified


def _verified_accesses_cover_resources(
    accesses: tuple[Any, ...],
    verified: tuple[str, ...],
) -> bool:
    return all(
        any(resources_overlap(access.name, resource) for resource in verified)
        for access in accesses
    )


def verified_code_abstention_proves_waiver(
    authority: Any,
    *,
    effect: str,
    observation: Mapping[str, Any],
) -> bool:
    """Prove the narrow runtime-authored NO_CHANGE waiver predicate.

    This remains separate from the generic terminal evidence helper because
    that helper has no concrete effect obligation.
    """

    if effect != "write":
        return False
    semantics = _write_code_semantics(authority, observation)
    if semantics is None:
        return False
    verified_evidence = _valid_seal_evidence(
        _code_outcome_parts(observation),
        _workspace_root(authority),
    )
    if verified_evidence is None:
        return False
    _seal, _manifest, _leaf_records, verified = verified_evidence
    return _verified_accesses_cover_resources(
        tuple(semantics.resource_access),
        verified,
    )


def _active_write_intents(
    owner: Any,
    predicate_resolutions: Any,
) -> tuple[tuple[str, ...], tuple[Any, ...]] | None:
    intents = tuple(
        item
        for item in getattr(owner, "effect_intents", ())
        if item.effect == "write" and not getattr(item, "prohibited", False)
    )
    if not intents:
        return None
    from agent.planning.effect_intent import effect_intent_matches

    active: list[str] = []
    for intent in intents:
        target = normalize_resource_id(
            getattr(intent, "target", WORKSPACE_RESOURCE)
        )
        if target == WORKSPACE_RESOURCE:
            return None
        target_access = ResourceAccess(
            target,
            ResourceMode.WRITE,
            ResourceProvenance.TRUSTED_DERIVED,
        )
        if effect_intent_matches(
            intent,
            "write",
            target_access,
            predicate_resolutions=predicate_resolutions,
        ):
            active.append(target)
    if not active:
        return None
    return tuple(active), intents


def _active_accesses_match_intents(
    semantics: Any,
    intents: tuple[Any, ...],
    predicate_resolutions: Any,
) -> bool:
    from agent.planning.effect_intent import effect_intent_matches

    return all(
        any(
            effect_intent_matches(
                intent,
                "write",
                access,
                predicate_resolutions=predicate_resolutions,
            )
            for intent in intents
        )
        for access in semantics.resource_access
    )


def verified_code_abstention_scope_covers_pending_write(
    owner: Any,
    authority: Any,
    observation: Mapping[str, Any],
) -> bool:
    """Apply the owner-specific finite scope guard before waive_effect."""

    if not verified_code_abstention_proves_waiver(
        authority,
        effect="write",
        observation=observation,
    ):
        return False
    pending_effects = tuple(getattr(owner, "pending_effects", lambda: ())())
    if "write" not in pending_effects:
        return False
    requirement_state = getattr(
        owner,
        "_effect_requirement_state",
        lambda _effect: "active",
    )
    if requirement_state("write") != "active":
        return False
    parts = _code_outcome_parts(observation)
    if parts is None:
        return False
    seal, _manifest = parts
    verified = tuple(normalize_resource_id(item) for item in seal.verified_resources)
    predicate_resolutions = getattr(owner, "predicate_resolutions", None)
    active_intents = _active_write_intents(owner, predicate_resolutions)
    if active_intents is None:
        return False
    targets, intents = active_intents
    if any(
        not any(resources_overlap(target, resource) for resource in verified)
        for target in targets
    ):
        return False
    semantics = _invocation_semantics(authority, observation)
    if semantics is None:
        return False
    return _active_accesses_match_intents(
        semantics,
        intents,
        predicate_resolutions,
    )

__all__ = ["code_outcome_seal_candidate", "verified_code_abstention_proves_waiver", "verified_code_abstention_scope_covers_pending_write"]

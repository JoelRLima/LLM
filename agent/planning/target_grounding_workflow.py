"""Workflow orchestration for admitted-target grounding."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

from agent.resources.contracts import normalize_resource_id
from agent.runtime.path_safety import WorkspacePathError, assert_path_safe

from .intent_admission import AdmittedIntent, AuthorityEnvelope
from .target_grounding_model import GroundedTarget, GroundedTargetSet, GroundingError


def _memory_target(
    selector: Any,
    envelope: AuthorityEnvelope,
    identity: str,
    freshness_token: Callable[..., str],
) -> GroundedTarget:
    resource = normalize_resource_id(selector.value)
    if resource != "memory":
        raise GroundingError("GROUNDING_MEMORY_RESOURCE_INVALID")
    return GroundedTarget(
        selector.selector_id,
        resource,
        selector.kind,
        None,
        "trusted-memory-resource",
        (),
        None,
        None,
        None,
        identity,
        freshness_token(
            resource=resource,
            source_sha256=None,
            definition_line=None,
            definition_column=None,
        ),
        envelope.allows_write(resource),
    )


def _needs_grounding(selector: Any, mutation_selector_ids: set[str]) -> bool:
    return (
        selector.selector_id in mutation_selector_ids
        or selector.symbolic
        or selector.literal_resource is not None
    )


def _ensure_mutation_targets(
    grounded: list[GroundedTarget],
    mutation_selector_ids: set[str],
) -> None:
    if mutation_selector_ids and not mutation_selector_ids.issubset(
        {item.selector_id for item in grounded}
    ):
        raise GroundingError("GROUNDING_TARGET_REQUIRED")


def _mutation_selector_ids(intent: AdmittedIntent) -> set[str]:
    return {
        selector_id
        for effect in intent.requested_effects
        if effect.effect in {"write", "memory_write"}
        for selector_id in effect.selector_ids
    }


def _ground_selector(
    selector: Any,
    envelope: AuthorityEnvelope,
    root: Path,
    identity: str,
    mutation_selector_ids: set[str],
    *,
    max_files: int,
    max_source_bytes: int,
    max_total_source_bytes: int,
    max_enumerated_paths: int,
    max_candidates: int,
    candidate_for_selector: Callable[..., tuple[GroundedTarget, ...]],
    freshness_token: Callable[..., str],
) -> tuple[GroundedTarget, ...]:
    if selector.role == "memory":
        target = _memory_target(selector, envelope, identity, freshness_token)
        selected: tuple[GroundedTarget, ...] = (target,)
    else:
        selected = candidate_for_selector(
            selector,
            envelope=envelope,
            root=root,
            root_identity=identity,
            max_files=max_files,
            max_source_bytes=max_source_bytes,
            max_total_source_bytes=max_total_source_bytes,
            max_enumerated_paths=max_enumerated_paths,
            max_candidates=max_candidates,
        )
    if selector.selector_id in mutation_selector_ids and any(
        not item.mutation_authorized for item in selected
    ):
        raise GroundingError("GROUNDING_OUTSIDE_WRITE_SCOPE")
    # Write scope is only a limiting condition.  It cannot manufacture
    # mutation intent for source/topic/read selectors that were not explicitly
    # bound to a requested durable effect.
    if selector.selector_id not in mutation_selector_ids:
        return tuple(replace(item, mutation_authorized=False) for item in selected)
    return selected


def ground_admitted_targets(
    intent: AdmittedIntent,
    envelope: AuthorityEnvelope,
    workspace_root: str | Path | None,
    *,
    max_files: int,
    max_source_bytes: int,
    max_total_source_bytes: int,
    max_enumerated_paths: int,
    max_candidates: int,
    candidate_for_selector: Callable[..., tuple[GroundedTarget, ...]],
    root_identity: Callable[[Path], str],
    freshness_token: Callable[..., str],
) -> GroundedTargetSet:
    """Ground every admitted selector that can influence a durable effect."""

    if not isinstance(intent, AdmittedIntent):
        raise GroundingError("GROUNDING_INTENT_INVALID")
    if not isinstance(envelope, AuthorityEnvelope):
        raise GroundingError("GROUNDING_AUTHORITY_INVALID")
    root_value = workspace_root or envelope.workspace_root
    if root_value is None:
        raise GroundingError("GROUNDING_WORKSPACE_REQUIRED")
    root = Path(root_value).expanduser().resolve()
    try:
        assert_path_safe(root, directory=True)
    except (OSError, RuntimeError, ValueError, WorkspacePathError) as exc:
        raise GroundingError("GROUNDING_WORKSPACE_INVALID") from exc
    if min(
        max_files,
        max_source_bytes,
        max_total_source_bytes,
        max_enumerated_paths,
        max_candidates,
    ) <= 0:
        raise GroundingError("GROUNDING_LIMIT_INVALID")
    identity = root_identity(root)
    mutation_selector_ids = _mutation_selector_ids(intent)
    grounded: list[GroundedTarget] = []
    for selector in intent.selectors:
        if not _needs_grounding(selector, mutation_selector_ids):
            continue
        selected = _ground_selector(
            selector,
            envelope,
            root,
            identity,
            mutation_selector_ids,
            max_files=max_files,
            max_source_bytes=max_source_bytes,
            max_total_source_bytes=max_total_source_bytes,
            max_enumerated_paths=max_enumerated_paths,
            max_candidates=max_candidates,
            candidate_for_selector=candidate_for_selector,
            freshness_token=freshness_token,
        )
        grounded.extend(selected)
    _ensure_mutation_targets(grounded, mutation_selector_ids)
    return GroundedTargetSet(
        tuple(grounded),
        identity,
        envelope.authority_identity,
        tuple(sorted(mutation_selector_ids)),
        max_files,
        max_source_bytes,
        max_total_source_bytes,
        max_enumerated_paths,
        max_candidates,
    )

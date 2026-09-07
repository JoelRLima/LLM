"""Freshness and read-scope checks for already grounded targets."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from agent.capabilities import WRITE_CAPABILITIES, canonical_capabilities
from agent.resources.contracts import normalize_resource_id, resource_is_within
from agent.runtime.path_safety import (
    WorkspacePathError,
    assert_path_safe,
    resolve_workspace_path,
    workspace_relative_path,
)

from .intent_admission import AuthorityEnvelope
from .target_grounding_ast import locations_for_source
from .target_grounding_discovery import discover_symbol_definitions
from .target_grounding_global import revalidate_global_symbol_uniqueness
from .target_grounding_model import GroundedTarget, GroundedTargetSet, GroundingError


def _root_identity(root: Path) -> str:
    return hashlib.sha256(root.as_posix().encode("utf-8")).hexdigest()


def _bytes_sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _current_target_path(target: GroundedTarget, root: Path) -> Path:
    try:
        path = resolve_workspace_path(root, target.resource)
        assert_path_safe(path, directory=False)
        if workspace_relative_path(root, path) != target.resource:
            raise GroundingError("GROUNDING_PATH_MOVED")
    except GroundingError:
        raise
    except (OSError, RuntimeError, ValueError, WorkspacePathError) as exc:
        raise GroundingError("GROUNDING_PATH_ESCAPED") from exc
    return path


def _assert_read_scope(target: GroundedTarget, envelope: AuthorityEnvelope | None) -> None:
    resource = normalize_resource_id(target.resource)
    if resource == "memory":
        if isinstance(envelope, AuthorityEnvelope):
            if not envelope.has_capability("memory") or not envelope.allows_read("memory"):
                raise GroundingError("GROUNDING_MEMORY_AUTHORITY_DENIED")
        return
    read_scope = (
        tuple(item.name for item in envelope.read_resources)
        if isinstance(envelope, AuthorityEnvelope)
        else target.discovery_scope
    )
    if isinstance(envelope, AuthorityEnvelope):
        allowed = envelope.allows_read(resource)
    else:
        allowed = any(resource_is_within(scope, resource) for scope in read_scope)
    if not allowed:
        raise GroundingError("GROUNDING_READ_SCOPE_DENIED")


def _assert_required_current_authority(
    envelope: AuthorityEnvelope,
    *,
    admitted_intent: Any,
    required_capabilities: Iterable[Any],
    required_effects: Iterable[str],
) -> None:
    try:
        required = canonical_capabilities(required_capabilities)
    except (TypeError, ValueError) as exc:
        raise GroundingError("GROUNDING_CAPABILITY_REQUIREMENT_INVALID") from exc
    if not required.issubset(canonical_capabilities(envelope.parent_permissions)):
        raise GroundingError("GROUNDING_CAPABILITY_DENIED")
    effects = {str(item).strip().casefold() for item in required_effects}
    if admitted_intent is not None:
        effects.update(
            str(item.effect).strip().casefold()
            for item in getattr(admitted_intent, "requested_effects", ())
        )
    if not effects.issubset(envelope.granted_effects):
        raise GroundingError("GROUNDING_EFFECT_NOT_GRANTED")


def _assert_memory_current_authority(
    target: GroundedTarget,
    envelope: AuthorityEnvelope,
    admitted_intent: Any,
) -> None:
    resource = normalize_resource_id(target.resource)
    if not envelope.has_capability("memory") or not envelope.allows_read(resource):
        raise GroundingError("GROUNDING_MEMORY_AUTHORITY_DENIED")
    if target.mutation_authorized and not envelope.allows_write(resource):
        raise GroundingError("GROUNDING_MEMORY_WRITE_SCOPE_DENIED")
    if target.mutation_authorized and admitted_intent is not None:
        selectors = set(getattr(admitted_intent, "mutation_selector_ids", ()))
        if target.selector_id not in selectors:
            raise GroundingError("GROUNDING_MUTATION_BINDING_INVALID")


def _assert_filesystem_current_authority(
    target: GroundedTarget,
    envelope: AuthorityEnvelope,
    admitted_intent: Any,
) -> None:
    if not any(envelope.has_capability(item) for item in WRITE_CAPABILITIES):
        raise GroundingError("GROUNDING_WRITE_CAPABILITY_DENIED")
    if not envelope.allows_write(target.resource):
        raise GroundingError("GROUNDING_WRITE_SCOPE_DENIED")
    if admitted_intent is not None:
        selectors = set(getattr(admitted_intent, "mutation_selector_ids", ()))
        if target.selector_id not in selectors:
            raise GroundingError("GROUNDING_MUTATION_BINDING_INVALID")


def _assert_current_authority(
    target: GroundedTarget,
    envelope: AuthorityEnvelope | None,
    *,
    admitted_intent: Any = None,
    required_capabilities: Iterable[Any] = (),
    required_effects: Iterable[str] = (),
) -> None:
    """Re-prove current capability/effect/scope facts before a mutation."""

    if envelope is None:
        # Direct legacy callers can still inspect freshness.  The W14
        # mutation binder always supplies the current envelope and therefore
        # cannot use this compatibility branch for a marked task.
        return
    resource = normalize_resource_id(target.resource)
    _assert_required_current_authority(
        envelope,
        admitted_intent=admitted_intent,
        required_capabilities=required_capabilities,
        required_effects=required_effects,
    )
    if resource == "memory":
        _assert_memory_current_authority(target, envelope, admitted_intent)
        return
    if target.mutation_authorized:
        _assert_filesystem_current_authority(target, envelope, admitted_intent)


def _assert_symbol_fresh(target: GroundedTarget, source: bytes | None) -> None:
    if target.symbol is None:
        return
    locations = locations_for_source(source or b"", target.symbol, target.resource)
    if source is None or len(locations) != 1:
        raise GroundingError("GROUNDING_STALE")
    location = locations[0]
    if (location[0], location[1]) != (target.definition_line, target.definition_column):
        raise GroundingError("GROUNDING_STALE")


def revalidate_grounded_target(
    target: GroundedTarget,
    workspace_root: str | Path,
    *,
    envelope: AuthorityEnvelope | None = None,
    admitted_intent: Any = None,
    required_capabilities: Iterable[Any] = (),
    required_effects: Iterable[str] = (),
) -> GroundedTarget:
    """Reject stale, moved, replaced, or link-escaped grounding evidence."""

    if not isinstance(target, GroundedTarget):
        raise GroundingError("GROUNDING_RECORD_INVALID")
    _assert_read_scope(target, envelope)
    _assert_current_authority(
        target,
        envelope,
        admitted_intent=admitted_intent,
        required_capabilities=required_capabilities,
        required_effects=required_effects,
    )
    # Logical memory is not a filesystem resource.  In particular, do not
    # resolve, stat, read, or symlink-check a path named ``memory``.
    if normalize_resource_id(target.resource) == "memory":
        if target.source_sha256 is not None:
            raise GroundingError("GROUNDING_MEMORY_RECORD_INVALID")
        return target
    root = Path(workspace_root).expanduser().resolve()
    if _root_identity(root) != target.workspace_root_identity:
        raise GroundingError("GROUNDING_ROOT_CHANGED")
    path = _current_target_path(target, root)
    try:
        source = path.read_bytes() if path.is_file() else None
    except (OSError, RuntimeError) as exc:
        raise GroundingError("GROUNDING_SOURCE_UNREADABLE") from exc
    digest = _bytes_sha256(source) if source is not None else None
    if digest != target.source_sha256:
        raise GroundingError("GROUNDING_STALE")
    _assert_symbol_fresh(target, source)
    return target


def revalidate_grounded_targets(
    targets: GroundedTargetSet,
    workspace_root: str | Path,
    *,
    envelope: AuthorityEnvelope | None = None,
    admitted_intent: Any = None,
    required_capabilities: Iterable[Any] = (),
    required_effects: Iterable[str] = (),
) -> GroundedTargetSet:
    if not isinstance(targets, GroundedTargetSet):
        raise GroundingError("GROUNDING_SET_INVALID")
    if isinstance(envelope, AuthorityEnvelope):
        if targets.authority_identity != envelope.authority_identity:
            raise GroundingError("GROUNDING_AUTHORITY_CHANGED")
        if (
            admitted_intent is not None
            and getattr(admitted_intent, "authority_identity", None)
            != envelope.authority_identity
        ):
            raise GroundingError("GROUNDING_AUTHORITY_CHANGED")
        _revalidate_global_symbol_uniqueness(
            targets,
            workspace_root,
            envelope,
        )
    for target in targets.targets:
        revalidate_grounded_target(
            target,
            workspace_root,
            envelope=envelope,
            admitted_intent=admitted_intent,
            required_capabilities=required_capabilities,
            required_effects=required_effects,
        )
    return targets


def _revalidate_global_symbol_uniqueness(
    targets: GroundedTargetSet,
    workspace_root: str | Path,
    envelope: AuthorityEnvelope,
) -> None:
    """Delegate to the canonical bounded discovery owner."""

    revalidate_global_symbol_uniqueness(
        targets,
        workspace_root,
        envelope,
        discovery_owner=discover_symbol_definitions,
    )


def revalidate_grounded_authority(
    targets: GroundedTargetSet,
    *,
    envelope: AuthorityEnvelope | None,
    admitted_intent: Any = None,
    required_capabilities: Iterable[Any] = (),
    required_effects: Iterable[str] = (),
) -> GroundedTargetSet:
    """Recheck current logical authority without touching the filesystem."""

    if not isinstance(targets, GroundedTargetSet):
        raise GroundingError("GROUNDING_SET_INVALID")
    if isinstance(envelope, AuthorityEnvelope):
        if targets.authority_identity != envelope.authority_identity:
            raise GroundingError("GROUNDING_AUTHORITY_CHANGED")
        if (
            admitted_intent is not None
            and getattr(admitted_intent, "authority_identity", None)
            != envelope.authority_identity
        ):
            raise GroundingError("GROUNDING_AUTHORITY_CHANGED")
    for target in targets.targets:
        _assert_read_scope(target, envelope)
        _assert_current_authority(
            target,
            envelope,
            admitted_intent=admitted_intent,
            required_capabilities=required_capabilities,
            required_effects=required_effects,
        )
    return targets


__all__ = [
    "revalidate_grounded_authority",
    "revalidate_grounded_target",
    "revalidate_grounded_targets",
]

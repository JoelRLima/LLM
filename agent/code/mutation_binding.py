from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from agent.planning.intent_admission import AdmittedIntent, AuthorityEnvelope
from agent.planning.target_grounding import (
    GroundedTargetSet,
    GroundingError,
    revalidate_grounded_targets,
)
from agent.planning.target_grounding_revalidation import revalidate_grounded_authority
from agent.resources.contracts import normalize_resource_id, resource_is_within
from agent.runtime.path_safety import WorkspacePathError, workspace_relative_path


class MutationBindingError(ValueError):
    """Raised when an observed or proposed effect exceeds admitted targets."""
    def __init__(self, reason_code: str, detail: str | None = None) -> None:
        self.reason_code = reason_code
        self.code = reason_code
        super().__init__(reason_code if detail is None else f"{reason_code}: {detail}")


def _context_metadata(service: Any) -> Mapping[str, Any]:
    context = getattr(service, "context", None)
    metadata = getattr(context, "metadata", {})
    return metadata if isinstance(metadata, Mapping) else {}


def _w14_marked(service: Any) -> bool:
    if _context_metadata(service).get("w14_semantic_task") is True:
        return True
    owner = getattr(service, "orchestrator", None)
    state = getattr(owner, "agent_state", None)
    return getattr(state, "w14_semantic_task", False) is True


def _require_w14_projection(service: Any) -> tuple[AdmittedIntent, GroundedTargetSet]:
    metadata = _context_metadata(service)
    admitted = metadata.get("admitted_intent")
    grounded = metadata.get("grounded_target_set") or metadata.get("grounded_targets")
    envelope = metadata.get("authority_envelope")
    if (
        not isinstance(admitted, AdmittedIntent)
        or not isinstance(grounded, GroundedTargetSet)
        or not isinstance(envelope, AuthorityEnvelope)
    ):
        raise MutationBindingError(
            "MUTATION_W14_AUTHORITY_MISSING",
            "W14 semantic task lacks typed admitted/grounded authority",
        )
    return admitted, grounded


def _emit_audit(service: Any, event_type: str, data: Mapping[str, Any]) -> None:
    context = getattr(service, "context", None)
    emit = getattr(context, "emit", None)
    if callable(emit):
        try:
            emit(event_type, dict(data))
        except (RuntimeError, TypeError, ValueError):
            return


def _resources_from_authority(value: Any) -> tuple[str, ...] | None:
    if isinstance(value, GroundedTargetSet):
        return tuple(value.mutation_targets)
    if isinstance(value, AdmittedIntent):
        return tuple(value.mutation_targets)
    if hasattr(value, "mutation_targets"):
        return _resources_from_authority(value.mutation_targets)
    if isinstance(value, Mapping):
        return _resources_from_mapping(value)
    if isinstance(value, (str, bytes, bytearray)):
        return (normalize_resource_id(value),)
    if isinstance(value, Iterable):
        return _resources_from_iterable(value)
    return None


def _resources_from_mapping(value: Mapping[Any, Any]) -> tuple[str, ...] | None:
    for key in ("mutation_targets", "grounded_targets", "admitted_targets"):
        if key in value:
            return _resources_from_authority(value[key])
    return None


def _resources_from_iterable(value: Iterable[Any]) -> tuple[str, ...]:
    values: list[str] = []
    for item in value:
        if isinstance(item, str):
            values.append(normalize_resource_id(item))
        elif hasattr(item, "resource"):
            values.append(normalize_resource_id(item.resource))
        elif hasattr(item, "canonical_resource"):
            values.append(normalize_resource_id(item.canonical_resource))
    return tuple(dict.fromkeys(values))


def admitted_mutation_targets(service: Any) -> tuple[str, ...] | None:
    """Return the trusted target projection, if this task carries one."""
    metadata = _context_metadata(service)
    candidates: list[Any] = []
    if isinstance(metadata, Mapping):
        candidates.extend(
            metadata.get(key)
            for key in ("grounded_targets", "grounded_target_set", "admitted_intent")
            if key in metadata
        )
    candidates.extend(
        getattr(service, key, None)
        for key in ("grounded_targets", "grounded_target_set", "admitted_intent")
        if getattr(service, key, None) is not None
    )
    if not candidates:
        return None
    for candidate in candidates:
        resources = _resources_from_authority(candidate)
        if resources is not None:
            return tuple(dict.fromkeys(resources))
    return ()


def changeset_resources(change_set: Any, root: str | Path | None = None) -> tuple[str, ...]:
    """Project every source and destination path as a canonical resource."""

    resources: list[str] = []
    workspace = Path(root).resolve() if root is not None else None
    for change in getattr(change_set, "changes", ()):
        for value in (getattr(change, "path", None), getattr(change, "destination_path", None)):
            if not isinstance(value, str) or not value.strip():
                continue
            try:
                normalized = (
                    workspace_relative_path(workspace, value)
                    if workspace is not None
                    else normalize_resource_id(value)
                )
            except (OSError, RuntimeError, ValueError, WorkspacePathError) as exc:
                raise MutationBindingError("MUTATION_TARGET_PATH_INVALID") from exc
            resources.append(normalize_resource_id(normalized))
    return tuple(dict.fromkeys(resources))


def preview_resources(preview: Any) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            normalize_resource_id(value)
            for value in getattr(preview, "affected_files", ()) or ()
            if isinstance(value, str) and value.strip()
        )
    )


def assert_resources_subset(
    observed: Iterable[str],
    admitted: Iterable[str],
    *,
    reason_code: str = "MUTATION_TARGET_OUTSIDE_ADMITTED",
) -> tuple[str, ...]:
    allowed = tuple(dict.fromkeys(normalize_resource_id(item) for item in admitted))
    selected = tuple(dict.fromkeys(normalize_resource_id(item) for item in observed))
    if not allowed and selected:
        raise MutationBindingError(reason_code, "no admitted mutation target exists")
    outside = tuple(
        item
        for item in selected
        if not any(resource_is_within(candidate, item) for candidate in allowed)
    )
    if outside:
        raise MutationBindingError(reason_code, ", ".join(outside))
    return selected


def assert_changeset_admitted(
    service: Any,
    change_set: Any,
    *,
    preview: Any | None = None,
    revalidate: bool = False,
) -> tuple[str, ...] | None:
    """Enforce target subset before approval and mutation."""

    if _w14_marked(service):
        admitted_intent, grounded_projection = _require_w14_projection(service)
    else:
        admitted_intent = None
        grounded_projection = None
    admitted = admitted_mutation_targets(service)
    if admitted is None:
        return None
    metadata = _context_metadata(service)
    if revalidate:
        grounded = grounded_projection
        if grounded is None:
            grounded = metadata.get("grounded_target_set") or metadata.get("grounded_targets")
        if isinstance(grounded, GroundedTargetSet):
            try:
                revalidate_grounded_targets(
                    grounded,
                    service.root,
                    envelope=metadata.get("authority_envelope"),
                    admitted_intent=admitted_intent or metadata.get("admitted_intent"),
                    required_capabilities=metadata.get(
                        "invocation_required_capabilities", ()
                    ),
                    required_effects=metadata.get("invocation_durable_effects", ()),
                )
                revalidate_grounded_authority(
                    admitted_intent=admitted_intent or metadata.get("admitted_intent"),
                    targets=grounded,
                    envelope=metadata.get("authority_envelope"),
                    required_capabilities=metadata.get(
                        "invocation_required_capabilities", ()
                    ),
                    required_effects=metadata.get("invocation_durable_effects", ()),
                )
            except GroundingError as exc:
                _emit_audit(
                    service,
                    "mutation_subset_checked",
                    {"subset": False, "reason_code": exc.reason_code, "revalidated": True},
                )
                raise MutationBindingError("MUTATION_GROUNDING_STALE", str(exc)) from exc
    proposed = changeset_resources(change_set, getattr(service, "root", None))
    try:
        assert_resources_subset(proposed, admitted)
        if preview is not None:
            assert_resources_subset(preview_resources(preview), admitted)
    except MutationBindingError as exc:
        _emit_audit(
            service,
            "mutation_subset_checked",
            {
                "subset": False,
                "reason_code": exc.reason_code,
                "observed_resources": list(proposed),
                "admitted_resources": list(admitted),
            },
        )
        raise
    _emit_audit(
        service,
        "mutation_subset_checked",
        {
            "subset": True,
            "observed_resources": list(proposed),
            "admitted_resources": list(admitted),
            "revalidated": bool(revalidate),
        },
    )
    return proposed


def assert_result_mutation_admitted(service: Any, result: Any) -> tuple[str, ...] | None:
    """Check the observed affected-file projection before reporting success."""

    if _w14_marked(service):
        _require_w14_projection(service)
    admitted = admitted_mutation_targets(service)
    if admitted is None:
        return None
    observed: list[str] = []
    for artifact in getattr(result, "artifacts", ()) or ():
        metadata = getattr(artifact, "metadata", {})
        if not isinstance(metadata, Mapping):
            continue
        for key in ("affected_files", "surviving_files", "attempted_files"):
            values = metadata.get(key, ())
            if isinstance(values, str):
                values = (values,)
            if isinstance(values, Iterable):
                observed.extend(item for item in values if isinstance(item, str))
    if not observed:
        _emit_audit(service, "mutation_subset_checked", {"subset": True, "observed_resources": []})
        return ()
    try:
        selected = assert_resources_subset(observed, admitted, reason_code="MUTATION_OBSERVED_OUTSIDE_ADMITTED")
    except MutationBindingError as exc:
        _emit_audit(
            service,
            "mutation_subset_checked",
            {"subset": False, "reason_code": exc.reason_code},
        )
        raise
    _emit_audit(
        service,
        "mutation_subset_checked",
        {"subset": True, "observed_resources": list(selected), "admitted_resources": list(admitted)},
    )
    return selected


__all__ = ("MutationBindingError", "admitted_mutation_targets", "assert_changeset_admitted", "assert_result_mutation_admitted", "assert_resources_subset", "changeset_resources", "preview_resources")

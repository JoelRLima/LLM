"""Bounded, read-only audit projections for one runtime attempt.

This module owns only projection.  It does not execute a model/tool, make an
approval decision, mutate runtime state, or become an authority owner.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from typing import Any

from agent.llm.identity import (
    declared_model_audit_identity,
    observed_provider_model_id,
)
from agent.observability.audit_projection_artifacts import (
    _artifact_objects,
    _project_one_artifact,
)
from agent.observability.audit_projection_fields import (
    APPROVAL_DISPOSITIONS,
    MAX_AUDIT_ARTIFACTS,
    MAX_AUDIT_CAPABILITIES,
    MAX_AUDIT_MODEL_IDS,
    MAX_AUDIT_PATHS,
    MAX_AUDIT_TEXT,
    MAX_AUDIT_TOOLS,
    _bounded_text,
    _event_parts,
    _safe_string,
    project_approval_disposition,
    project_effect,
    project_required_capabilities,
)
from agent.observability.audit_projection_runtime import (
    RunAuditReceipt,
    _authority_projection,
    _metrics_for,
    _runtime_profile,
    _terminal_projection,
    _tool_identity_events,
)
from agent.tools.contracts import ToolOriginKind

AUDIT_RECEIPT_SCHEMA_VERSION = 1

def project_model_identity(
    profile: Any = None,
    gateway: Any = None,
    config: Mapping[str, Any] | None = None,
) -> dict[str, str | None]:
    """Project only the declared, bounded model identity fields."""
    return dict(declared_model_audit_identity(gateway, profile, config))


def project_tool_descriptor(descriptor: Any) -> dict[str, Any] | None:
    """Project the descriptor identity without its schema or description."""

    if descriptor is None:
        return None
    if isinstance(descriptor, Mapping):
        get = descriptor.get
    else:
        def get(key: str, default: Any = None) -> Any:
            return getattr(descriptor, key, default)
    origin = get("origin_kind")
    origin_value = getattr(origin, "value", origin)
    if origin_value not in {item.value for item in ToolOriginKind}:
        origin_value = _bounded_text(origin_value)
    extension_id = _safe_string(get("extension_id"))
    if origin_value != ToolOriginKind.EXTENSION.value:
        extension_id = None
    return {
        "name": _safe_string(get("name")),
        "origin_kind": origin_value,
        "adapter_id": _safe_string(get("adapter_id")),
        "extension_id": extension_id,
        "source_version": _safe_string(get("source_version")),
        "protocol_version": _safe_string(get("protocol_version")),
    }

def project_artifact_refs(
    result: Any,
    workspace_root: str | os.PathLike[str] | None = None,
) -> tuple[dict[str, Any], ...]:
    refs: list[dict[str, Any]] = []
    for artifact in _artifact_objects(result):
        projected = _project_one_artifact(artifact, workspace_root)
        if projected is not None:
            refs.append(projected)
        if len(refs) >= MAX_AUDIT_ARTIFACTS:
            break
    return tuple(refs)


def project_artifacts(
    result: Any,
    workspace_root: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    total = len(_artifact_objects(result))
    refs = project_artifact_refs(result, workspace_root)
    emitted = len(refs)
    omitted = max(0, total - emitted)
    return {
        "items": list(refs),
        "bounds": {
            "total": total,
            "emitted": emitted,
            "omitted": omitted,
            "truncated": omitted > 0,
        },
    }


def audit_event_fields(
    event_type: str,
    metadata: Mapping[str, Any] | None,
    *,
    result: Any = None,
    workspace_root: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Return safe semantic additions for an existing runtime event."""

    raw = metadata if isinstance(metadata, Mapping) else {}
    projected: dict[str, Any] = {
        "descriptor": project_tool_descriptor(raw.get("descriptor")),
        "required_capabilities": project_required_capabilities(raw.get("required_capabilities")),
        "approval_disposition": project_approval_disposition(raw.get("approval_disposition")),
    }
    if "requested_effects" in raw:
        projected["requested_effects"] = project_required_capabilities(raw.get("requested_effects"))
    if event_type == "tool_denied":
        projected["executed"] = False
    if event_type == "tool_end" and result is not None:
        projected["effect"] = project_effect(result, workspace_root)
        artifacts = project_artifacts(result, workspace_root)
        projected["artifact_refs"] = artifacts["items"]
        projected["artifact_bounds"] = artifacts["bounds"]
    return projected


def _metric_entries(metrics: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return [
        item
        for item in metrics
        if item.get("type") == "model_call" or item.get("metric_type") == "model_call"
    ]


def _append_identity(observed: list[str], identity: str | None) -> None:
    if identity is not None and identity not in observed:
        observed.append(identity)


def _completed_model_events(events: Sequence[Any]) -> list[Mapping[str, Any]]:
    completed: list[Mapping[str, Any]] = []
    for event in events:
        event_type, data = _event_parts(event)
        if event_type == "model_call_completed":
            completed.append(data)
    return completed


def _observed_model_projection(
    metrics: Sequence[Mapping[str, Any]],
    events: Sequence[Any],
) -> tuple[list[str], bool, int]:
    model_entries = _metric_entries(metrics)
    observed: list[str] = []
    for item in model_entries:
        identity = observed_provider_model_id(item.get("provider_metadata"))
        _append_identity(observed, identity if identity is not None else observed_provider_model_id(item))
    completed_events = _completed_model_events(events)
    for data in completed_events:
        _append_identity(observed, observed_provider_model_id(data.get("observed_provider_model_id")) or observed_provider_model_id(data))
    call_count = len(model_entries) or len(completed_events)
    complete = bool(call_count) and all(_metric_identity(item) is not None for item in model_entries)
    if not model_entries and completed_events:
        event_ids = [observed_provider_model_id(data) for data in completed_events]
        complete = bool(event_ids) and all(item is not None for item in event_ids)
    return observed, complete, call_count


def _metric_identity(item: Mapping[str, Any]) -> str | None:
    identity = observed_provider_model_id(item.get("provider_metadata"))
    return identity if identity is not None else observed_provider_model_id(item)


def build_run_audit_receipt(
    orchestrator: Any,
    snapshot: Any = None,
    *,
    metrics: Sequence[Mapping[str, Any]] | None = None,
    events: Sequence[Any] | None = None,
) -> RunAuditReceipt:
    """Build one bounded receipt from canonical sources without execution."""

    selected_snapshot = snapshot or getattr(orchestrator, "_canonical_run_snapshot", None)
    if selected_snapshot is None:
        raise ValueError("run audit receipt requires a canonical run snapshot")
    state = getattr(orchestrator, "agent_state", None)
    selected_events = list(events) if events is not None else list(getattr(state, "events", ()) or ())
    selected_metrics = _metrics_for(orchestrator, metrics)
    profile, gateway, config = _runtime_profile(orchestrator)
    declared_model = project_model_identity(profile, gateway, config)
    observed_ids, identity_complete, _model_call_count = _observed_model_projection(selected_metrics, selected_events)
    model_emitted = observed_ids[:MAX_AUDIT_MODEL_IDS]
    model_omitted = max(0, len(observed_ids) - len(model_emitted))
    tool_identities = _tool_identity_events(orchestrator, selected_events, project_tool_descriptor)
    tool_emitted = tool_identities[:MAX_AUDIT_TOOLS]
    tool_omitted = max(0, len(tool_identities) - len(tool_emitted))
    terminal = _terminal_projection(selected_snapshot)
    bounds = {
        "tool_identities_total": len(tool_identities),
        "tool_identities_emitted": len(tool_emitted),
        "tool_identities_omitted": tool_omitted,
        "observed_model_ids_total": len(observed_ids),
        "observed_model_ids_emitted": len(model_emitted),
        "observed_model_ids_omitted": model_omitted,
        "truncated": bool(tool_omitted or model_omitted),
    }
    model = {**declared_model, "observed_provider_model_ids": model_emitted, "observed_identity_complete": identity_complete}
    correlation = getattr(selected_snapshot, "correlation", None)
    return RunAuditReceipt(
        schema_version=AUDIT_RECEIPT_SCHEMA_VERSION,
        run_id=_safe_string(getattr(correlation, "run_id", None)),
        root_task_id=_safe_string(getattr(correlation, "root_task_id", None)),
        task_id=_safe_string(getattr(correlation, "task_id", None)),
        model=model,
        authority=_authority_projection(orchestrator),
        tools=tuple(tool_emitted),
        terminal=terminal,
        bounds=bounds,
    )


__all__ = [
    "APPROVAL_DISPOSITIONS",
    "AUDIT_RECEIPT_SCHEMA_VERSION",
    "MAX_AUDIT_ARTIFACTS",
    "MAX_AUDIT_CAPABILITIES",
    "MAX_AUDIT_MODEL_IDS",
    "MAX_AUDIT_PATHS",
    "MAX_AUDIT_TEXT",
    "MAX_AUDIT_TOOLS",
    "RunAuditReceipt",
    "audit_event_fields",
    "build_run_audit_receipt",
    "project_approval_disposition",
    "project_artifact_refs",
    "project_artifacts",
    "project_effect",
    "project_model_identity",
    "project_required_capabilities",
    "project_tool_descriptor",
]

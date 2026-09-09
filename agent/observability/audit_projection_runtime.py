"""Runtime-source projections for the bounded audit receipt."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from agent.observability.audit_projection_fields import (
    MAX_AUDIT_CAPABILITIES,
    _bounded_text,
    _event_parts,
    _freeze,
    _safe_capabilities,
    _safe_string,
    _thaw,
)


def _runtime_profile(orchestrator: Any) -> tuple[Any, Any, Mapping[str, Any] | None]:
    session = getattr(orchestrator, "session", None)
    profile = getattr(session, "model_profile", None) or getattr(orchestrator, "model_profile", None)
    gateway = getattr(session, "gateway", None) or getattr(orchestrator, "model_gateway", None)
    config = getattr(session, "config", None)
    return profile, gateway, config if isinstance(config, Mapping) else None


def _metrics_for(
    orchestrator: Any,
    supplied: Sequence[Mapping[str, Any]] | None,
) -> list[Mapping[str, Any]]:
    if supplied is not None:
        return [item for item in supplied if isinstance(item, Mapping)]
    reader = getattr(orchestrator, "_get_metrics_for_task", None)
    if not callable(reader):
        return []
    try:
        value = reader()
    except Exception:
        return []
    return [item for item in value if isinstance(item, Mapping)] if isinstance(value, Sequence) else []


def _authority_projection(orchestrator: Any) -> dict[str, Any]:
    application = getattr(orchestrator, "application_authority", None)
    task = getattr(orchestrator, "task_authority", None)
    runtime = getattr(application, "runtime_identity", None) or getattr(task, "runtime_identity", None)
    return {
        "application_snapshot_id": _safe_string(getattr(application, "snapshot_id", None)),
        "application_policy_version": _safe_string(getattr(application, "policy_version", None)),
        "application_provenance": _safe_string(getattr(application, "provenance", None)),
        "task_snapshot_id": _safe_string(getattr(task, "snapshot_id", None)),
        "task_policy_source": _safe_string(getattr(task, "policy_source", None)),
        "task_allowed_capabilities": sorted(
            _safe_capabilities(getattr(task, "allowed_capabilities", ())) or ()
        )[:MAX_AUDIT_CAPABILITIES],
        "runtime_identity": {
            "snapshot_id": _safe_string(getattr(runtime, "snapshot_id", None)),
            "workspace_id": _safe_string(getattr(runtime, "workspace_id", None)),
        },
    }


def _descriptor_for_event(
    data: Mapping[str, Any],
    registry: Any,
    descriptor_projector: Callable[[Any], dict[str, Any] | None],
) -> dict[str, Any] | None:
    tool_name = _safe_string(data.get("tool"))
    descriptor = descriptor_projector(data.get("descriptor"))
    if descriptor is None and registry is not None and tool_name:
        try:
            descriptor = descriptor_projector(registry.descriptor(tool_name))
        except (AttributeError, KeyError, TypeError):
            descriptor = None
    if descriptor is None and tool_name:
        return {
            "name": tool_name,
            "origin_kind": None,
            "adapter_id": None,
            "extension_id": None,
            "source_version": None,
            "protocol_version": None,
        }
    return descriptor


def _append_unique_descriptor(
    descriptor: dict[str, Any] | None,
    identities: list[dict[str, Any]],
    keys: set[str],
) -> None:
    if descriptor is None:
        return
    encoded = json.dumps(descriptor, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if encoded not in keys:
        keys.add(encoded)
        identities.append(descriptor)


def _history_descriptors(
    orchestrator: Any,
    registry: Any,
    descriptor_projector: Callable[[Any], dict[str, Any] | None],
    identities: list[dict[str, Any]],
    keys: set[str],
) -> None:
    state = getattr(orchestrator, "agent_state", None)
    history = getattr(state, "tool_history", ()) if state is not None else ()
    if not isinstance(history, Sequence):
        return
    for item in history:
        if not isinstance(item, Mapping) or registry is None:
            continue
        tool_name = _safe_string(item.get("tool"))
        if not tool_name:
            continue
        try:
            descriptor = descriptor_projector(registry.descriptor(tool_name))
        except (AttributeError, KeyError, TypeError):
            continue
        _append_unique_descriptor(descriptor, identities, keys)


def _tool_identity_events(
    orchestrator: Any,
    events: Sequence[Any],
    descriptor_projector: Callable[[Any], dict[str, Any] | None],
) -> list[dict[str, Any]]:
    registry = getattr(orchestrator, "tool_registry", None)
    identities: list[dict[str, Any]] = []
    keys: set[str] = set()
    for event in events:
        event_type, data = _event_parts(event)
        if event_type in {"tool_start", "tool_denied", "tool_end"}:
            _append_unique_descriptor(_descriptor_for_event(data, registry, descriptor_projector), identities, keys)
    if not identities:
        _history_descriptors(orchestrator, registry, descriptor_projector, identities, keys)
    return identities


def _terminal_projection(snapshot: Any) -> dict[str, Any]:
    status = _bounded_text(getattr(snapshot, "status", None)) or "unknown"
    failure = getattr(snapshot, "failure_fact", None)
    outcome = getattr(snapshot, "operational_outcome", None)
    layer = getattr(getattr(failure, "layer", None), "value", getattr(failure, "layer", None))
    return {
        "status": status,
        "success": status == "succeeded",
        "failure_code": _safe_string(getattr(failure, "code", None)),
        "failure_layer": _safe_string(layer),
        "mutation_occurred": bool(getattr(outcome, "mutation_occurred", False)),
        "validation_status": _bounded_text(getattr(outcome, "validation_status", None)),
        "final_state": _bounded_text(getattr(outcome, "final_state", None)),
    }


@dataclass(frozen=True, slots=True)
class RunAuditReceipt:
    """Frozen/versioned run-level audit projection."""

    schema_version: int
    run_id: str | None
    root_task_id: str | None
    task_id: str | None
    model: Mapping[str, Any]
    authority: Mapping[str, Any]
    tools: tuple[Mapping[str, Any], ...]
    terminal: Mapping[str, Any]
    bounds: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "model", _freeze(self.model))
        object.__setattr__(self, "authority", _freeze(self.authority))
        object.__setattr__(self, "tools", tuple(_freeze(item) for item in self.tools))
        object.__setattr__(self, "terminal", _freeze(self.terminal))
        object.__setattr__(self, "bounds", _freeze(self.bounds))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "root_task_id": self.root_task_id,
            "task_id": self.task_id,
            "model": _thaw(self.model),
            "authority": _thaw(self.authority),
            "tools": [_thaw(item) for item in self.tools],
            "terminal": _thaw(self.terminal),
            "bounds": _thaw(self.bounds),
        }

    def to_event_data(self) -> dict[str, Any]:
        """Drop envelope-owned identity fields before RuntimeEvent creation."""

        value = self.to_dict()
        for key in ("run_id", "root_task_id", "task_id"):
            value.pop(key, None)
        return value


__all__ = [
    "RunAuditReceipt",
    "_authority_projection",
    "_metrics_for",
    "_runtime_profile",
    "_terminal_projection",
    "_tool_identity_events",
]

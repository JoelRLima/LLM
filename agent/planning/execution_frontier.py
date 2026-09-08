"""Fresh bounded execution-frontier projection for live task decisions.

This module is intentionally a data-only boundary.  The frontier is rebuilt
from current runtime owners immediately before a consuming model request; it
does not own plan, semantic, authority, budget, workspace, or approval state.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from agent.planning.observation_receipts import canonical_source_identity
from agent.planning.progress_receipt import (
    MAX_CREDIT_PROJECTION,
    ProgressReceiptV1,
    build_progress_receipt,
)

FRONTIER_SCHEMA_VERSION = 1
MAX_NEXT_UNITS = 16
MAX_RUNNING_UNITS = 16
MAX_OBSERVATIONS = 24


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return tuple(sorted((_freeze(item) for item in value), key=str))
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _text(value: Any) -> str:
    return str(getattr(value, "value", value)).strip().replace("\\", "/")


def _status(value: Any) -> str:
    return _text(value).casefold()


def _mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        candidate = to_dict()
        if isinstance(candidate, Mapping):
            return candidate
    return {}


def _bounded_items(
    values: Sequence[Mapping[str, Any]],
    limit: int,
    *,
    ordering_identity: str,
    presentation_values: Sequence[Mapping[str, Any]] | None = None,
) -> "BoundedFrontierCollectionV1":
    if presentation_values is None:
        selected = tuple(_freeze(dict(item)) for item in values[:limit])
    else:
        selected = tuple(_freeze(dict(item)) for item in presentation_values[:limit])
    total = len(values)
    omitted = max(0, total - len(selected))
    truncated = omitted > 0
    return BoundedFrontierCollectionV1(
        items=selected,
        complete=not truncated,
        truncated=truncated,
        total_count=total,
        omitted_count=omitted,
        ordering_identity=ordering_identity,
    )


@dataclass(frozen=True, slots=True)
class BoundedFrontierCollectionV1:
    """Truthful bounded collection metadata used by the frontier."""

    items: tuple[Mapping[str, Any], ...] = ()
    complete: bool = True
    truncated: bool = False
    total_count: int = 0
    omitted_count: int = 0
    ordering_identity: str = "canonical_order"

    def __post_init__(self) -> None:
        if type(self.complete) is not bool or type(self.truncated) is not bool:
            raise ValueError("frontier collection flags must be strict booleans")
        if any(not isinstance(item, Mapping) for item in self.items):
            raise TypeError("frontier collection items must be mappings")
        total = self.total_count
        omitted = self.omitted_count
        if isinstance(total, bool) or not isinstance(total, int) or total < 0:
            raise ValueError("frontier total_count must be non-negative")
        if isinstance(omitted, bool) or not isinstance(omitted, int) or omitted < 0:
            raise ValueError("frontier omitted_count must be non-negative")
        if total < len(self.items) or omitted != total - len(self.items):
            raise ValueError("frontier collection counts are inconsistent")
        if self.truncated != (omitted > 0) or self.complete == self.truncated:
            raise ValueError("frontier collection completeness is inconsistent")
        object.__setattr__(self, "items", tuple(_freeze(item) for item in self.items))
        object.__setattr__(self, "ordering_identity", _text(self.ordering_identity))

    @property
    def total(self) -> int:
        return self.total_count

    @property
    def omitted(self) -> int:
        return self.omitted_count

    def to_dict(self) -> dict[str, Any]:
        return {
            "items": [_thaw(item) for item in self.items],
            "complete": self.complete,
            "truncated": self.truncated,
            "total_count": self.total_count,
            "omitted_count": self.omitted_count,
            "ordering_identity": self.ordering_identity,
        }


def _empty_collection() -> BoundedFrontierCollectionV1:
    return BoundedFrontierCollectionV1()


@dataclass(frozen=True, slots=True)
class ExecutionFrontierSnapshotV1:
    """Immutable current execution facts; never an authority object."""

    schema_version: int = FRONTIER_SCHEMA_VERSION
    task_identity: str | None = None
    task_definition_identity: str | None = None
    plan_identity: str | None = None
    current_step_id: str | None = None
    current_phase: str | None = None
    current_status: str | None = None
    next_units: BoundedFrontierCollectionV1 = field(default_factory=_empty_collection)
    running_units: BoundedFrontierCollectionV1 = field(default_factory=_empty_collection)
    terminal_units: BoundedFrontierCollectionV1 = field(default_factory=_empty_collection)
    observations: BoundedFrontierCollectionV1 = field(default_factory=_empty_collection)
    semantic_obligations: BoundedFrontierCollectionV1 = field(default_factory=_empty_collection)
    validation: Mapping[str, Any] = field(default_factory=dict)
    repository_state: Mapping[str, Any] = field(default_factory=dict)
    budgets: Mapping[str, Any] = field(default_factory=dict)
    recovery: Mapping[str, Any] = field(default_factory=dict)
    continuity_generation: int | None = None
    current_state_id: str | None = None
    progress_receipt_id: str | None = None
    credit_fact_projection: tuple[str, ...] = ()
    credit_fact_total_count: int = 0
    credit_fact_omitted_count: int = 0
    convergence: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.schema_version != FRONTIER_SCHEMA_VERSION:
            raise ValueError("unsupported ExecutionFrontierSnapshotV1 schema")
        for name in (
            "next_units",
            "running_units",
            "terminal_units",
            "observations",
            "semantic_obligations",
        ):
            value = getattr(self, name)
            if not isinstance(value, BoundedFrontierCollectionV1):
                raise TypeError(f"{name} must be BoundedFrontierCollectionV1")
        for name in (
            "validation",
            "repository_state",
            "budgets",
            "recovery",
            "convergence",
        ):
            value = getattr(self, name)
            if not isinstance(value, Mapping):
                raise TypeError(f"{name} must be a mapping")
            object.__setattr__(self, name, _freeze(value))
        if self.continuity_generation is not None and (
            isinstance(self.continuity_generation, bool)
            or not isinstance(self.continuity_generation, int)
            or self.continuity_generation < 0
        ):
            raise ValueError("continuity_generation must be a non-negative integer")
        object.__setattr__(self, "credit_fact_projection", tuple(str(item) for item in self.credit_fact_projection))
        total = max(self.credit_fact_total_count, len(self.credit_fact_projection))
        object.__setattr__(self, "credit_fact_total_count", total)
        object.__setattr__(self, "credit_fact_omitted_count", max(0, total - len(self.credit_fact_projection)))
        for name in (
            "task_identity",
            "task_definition_identity",
            "plan_identity",
            "current_step_id",
            "current_phase",
            "current_status",
            "current_state_id",
            "progress_receipt_id",
        ):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _text(value))

    @property
    def next_executable_unit_ids(self) -> tuple[str, ...]:
        return tuple(str(item.get("id", "")) for item in self.next_units.items if item.get("id"))

    @property
    def fresh_observation_count(self) -> int:
        return sum(1 for item in self.observations.items if item.get("freshness") in {"CURRENT", "CURRENT_RUNTIME_PROJECTION"})

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "task_identity": self.task_identity,
            "task_definition_identity": self.task_definition_identity,
            "plan_identity": self.plan_identity,
            "current_step_id": self.current_step_id,
            "current_phase": self.current_phase,
            "current_status": self.current_status,
            "next_units": self.next_units.to_dict(),
            "running_units": self.running_units.to_dict(),
            "terminal_units": self.terminal_units.to_dict(),
            "observations": self.observations.to_dict(),
            "semantic_obligations": self.semantic_obligations.to_dict(),
            "validation": _thaw(self.validation),
            "repository_state": _thaw(self.repository_state),
            "budgets": _thaw(self.budgets),
            "recovery": _thaw(self.recovery),
            "continuity_generation": self.continuity_generation,
            "current_state_id": self.current_state_id,
            "progress_receipt_id": self.progress_receipt_id,
            "credit_fact_projection": {
                "items": list(self.credit_fact_projection),
                "complete": self.credit_fact_omitted_count == 0,
                "truncated": self.credit_fact_omitted_count > 0,
                "total_count": self.credit_fact_total_count,
                "omitted_count": self.credit_fact_omitted_count,
            },
            "convergence": _thaw(self.convergence),
        }


def _unit_projection(state: Any, plan: Any, step_records: Mapping[str, Any] | None, graph_state: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    records = step_records if isinstance(step_records, Mapping) else getattr(state, "step_records", {})
    if not isinstance(records, Mapping):
        records = {}
    raw: list[tuple[str, str, str]] = []
    selected_graph = graph_state if graph_state is not None else getattr(state, "task_graph_state", None)
    graph_states = getattr(selected_graph, "states", None)
    if isinstance(graph_states, Mapping):
        for identifier, value in sorted(graph_states.items(), key=lambda item: str(item[0])):
            raw.append((_text(identifier), "graph", _status(value)))
    elif isinstance(selected_graph, Mapping) and isinstance(selected_graph.get("states"), Mapping):
        for identifier, value in sorted(selected_graph["states"].items(), key=lambda item: str(item[0])):
            raw.append((_text(identifier), "graph", _status(value)))
    else:
        selected_plan = plan if plan is not None else getattr(state, "plan", ())
        try:
            values = tuple(selected_plan or ())
        except TypeError:
            values = ()
        for index, step in enumerate(values):
            mapping = _mapping(step)
            identifier = _text(mapping.get("_step_id", mapping.get("step_id", getattr(step, "step_id", index))))
            record = records.get(identifier)
            record_map = _mapping(record)
            status = _status(record_map.get("status", getattr(record, "status", "pending"))) if record is not None else "pending"
            tool = _text(mapping.get("tool", getattr(step, "tool", "")))
            raw.append((identifier, tool, status))
    projected = [{"id": identifier, "tool": tool, "status": status} for identifier, tool, status in raw]
    next_units = [item for item in projected if item["status"] == "pending"]
    running_units = [item for item in projected if item["status"] == "running"]
    terminal_units = [
        item
        for item in projected
        if item["status"] in {"completed", "succeeded", "failed", "skipped", "blocked", "cancelled", "unverified"}
    ]
    return next_units, running_units, terminal_units


def _bounded_observation(
    value: Mapping[str, Any],
    *,
    workspace_root: Any = None,
) -> dict[str, Any]:
    allowed = (
        "source_id", "source_identity", "source_hash", "source_extent",
        "provenance", "freshness", "complete", "truncated",
        "reusable_exact_bytes", "classification", "pending_need",
    )
    result: dict[str, Any] = {}
    for key in allowed:
        if key not in value or key in {"content", "body", "text", "prompt", "credentials"}:
            continue
        selected = value[key]
        if key in {"source_id", "source_identity"}:
            selected = canonical_source_identity(selected, workspace_root=workspace_root)
        result[key] = _freeze(selected)
    return result


def _bounded_semantic(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: _freeze(value[key])
        for key in ("id", "kind", "status", "evidence_refs", "predicate_state")
        if key in value
    }


def _bounded_mapping(value: Any, allowed: Sequence[str]) -> Mapping[str, Any]:
    mapping = _mapping(value)
    return MappingProxyType(
        {
            key: _freeze(mapping[key])
            for key in allowed
            if key in mapping and key not in {"body", "content", "text", "prompt", "credentials", "workspace_root", "absolute_root"}
        }
    )


def build_execution_frontier(
    state: Any = None,
    *,
    plan: Any = None,
    step_records: Mapping[str, Any] | None = None,
    graph_state: Any = None,
    task_semantics: Any = None,
    observations: Sequence[Mapping[str, Any]] | None = None,
    observation_receipts: Sequence[Mapping[str, Any]] | None = None,
    repository_state: Mapping[str, Any] | None = None,
    validation: Mapping[str, Any] | None = None,
    budgets: Mapping[str, Any] | None = None,
    recovery: Mapping[str, Any] | None = None,
    continuity_generation: int | None = None,
    convergence: Mapping[str, Any] | None = None,
    progress_receipt: ProgressReceiptV1 | None = None,
    max_next_units: int = MAX_NEXT_UNITS,
    max_running_units: int = MAX_RUNNING_UNITS,
    max_observations: int = MAX_OBSERVATIONS,
    workspace_root: Any = None,
) -> ExecutionFrontierSnapshotV1:
    """Build one fresh frontier without I/O or authority mutation."""

    if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in (max_next_units, max_running_units, max_observations)):
        raise ValueError("frontier collection bounds must be non-negative integers")
    next_units, running_units, terminal_units = _unit_projection(state, plan, step_records, graph_state)
    semantics = task_semantics if task_semantics is not None else getattr(state, "task_semantics", None)
    explicit_observations = observation_receipts if observation_receipts is not None else observations
    progress = progress_receipt
    if progress is None:
        progress_kwargs: dict[str, Any] = {
            "plan": plan,
            "step_records": step_records,
            "graph_state": graph_state,
            "task_semantics": semantics,
        }
        if explicit_observations is not None:
            progress_kwargs["observations"] = explicit_observations
        progress = build_progress_receipt(state, **progress_kwargs)
    canonical_state = _mapping(progress.canonical_state)
    canonical_observations = canonical_state.get("observations", ())
    if isinstance(canonical_observations, Sequence) and not isinstance(canonical_observations, (str, bytes)):
        selected_observations = tuple(
            item for item in (_thaw(value) for value in canonical_observations)
            if isinstance(item, Mapping)
        )
    elif explicit_observations is not None:
        selected_observations = tuple(
            item for item in explicit_observations if isinstance(item, Mapping)
        )
    else:
        selected_observations = ()
    observation_values = [
        _bounded_observation(item, workspace_root=workspace_root)
        for item in selected_observations
    ]
    semantic_source = canonical_state.get("semantics", ())
    semantic_values = [
        _bounded_semantic(_mapping(item))
        for item in semantic_source
        if isinstance(item, Mapping)
    ] if isinstance(semantic_source, Sequence) else []
    observation_limit = min(max_observations, MAX_OBSERVATIONS)
    observation_presentation = observation_values[-observation_limit:] if observation_limit else []
    task_ref = getattr(state, "task_definition_ref", None) if state is not None else None
    task_ref_map = _mapping(task_ref)
    task_ref_identity = task_ref_map.get("fingerprint", task_ref_map.get("identity"))
    current_status = None
    last_result = getattr(state, "last_result", None) if state is not None else None
    if last_result is not None:
        current_status = _status(getattr(last_result, "status", _mapping(last_result).get("status", "")))
    phase = getattr(state, "current_phase", getattr(state, "phase", None)) if state is not None else None
    selected_convergence_source = convergence
    if selected_convergence_source is None and state is not None:
        convergence_owner = getattr(state, "convergence", None)
        snapshot = getattr(convergence_owner, "snapshot", None)
        if callable(snapshot):
            try:
                selected_convergence_source = snapshot()
            except Exception:
                selected_convergence_source = None
    selected_convergence = _bounded_mapping(
        selected_convergence_source,
        ("stage", "cycles_since_progress", "plateau_epoch_id", "refresh_performed_in_epoch", "replan_performed_in_epoch"),
    )
    selected_budgets = budgets
    if selected_budgets is None and state is not None:
        ledger = getattr(state, "budget_ledger", None)
        snapshot = getattr(ledger, "snapshot", None)
        if callable(snapshot):
            try:
                values = snapshot().to_dict()
            except Exception:
                values = {}
            if isinstance(values, Mapping):
                selected_budgets = {
                    "model_calls_remaining": max(
                        0, int(values.get("max_model_calls", 0)) - int(values.get("model_calls", 0)),
                    ),
                    "tool_calls_remaining": max(
                        0, int(values.get("max_task_tool_calls", 0)) - int(values.get("tool_calls", 0)),
                    ),
                    "input_tokens_remaining": max(
                        0, int(values.get("max_task_tokens", 0))
                        - int(values.get("accounted_tokens", 0)) - int(values.get("reserved_tokens", 0)),
                    ),
                }
    selected_recovery = recovery
    if selected_recovery is None and state is not None:
        recovery_owner = getattr(state, "recovery_budget", None)
        remaining = getattr(recovery_owner, "remaining_snapshot", None)
        if callable(remaining):
            try:
                selected_recovery = {"remaining": remaining()}
            except Exception:
                selected_recovery = None
    selected_continuity = continuity_generation
    if selected_continuity is None and state is not None:
        continuity = getattr(state, "continuity", None)
        continuity_map = _mapping(continuity)
        raw_generation = continuity_map.get("resume_generation")
        if isinstance(raw_generation, int) and not isinstance(raw_generation, bool):
            selected_continuity = raw_generation
    return ExecutionFrontierSnapshotV1(
        task_identity=_text(getattr(state, "root_task_id", "")) if state is not None and getattr(state, "root_task_id", None) else None,
        task_definition_identity=_text(task_ref_identity) if task_ref_identity else None,
        plan_identity=_text(getattr(state, "plan_identity", "")) if state is not None and getattr(state, "plan_identity", None) else None,
        current_step_id=_text(getattr(state, "current_step_id", "")) if state is not None and getattr(state, "current_step_id", None) else None,
        current_phase=_text(phase) if phase else None,
        current_status=current_status,
        next_units=_bounded_items(next_units, min(max_next_units, MAX_NEXT_UNITS), ordering_identity="plan_or_graph_order"),
        running_units=_bounded_items(running_units, min(max_running_units, MAX_RUNNING_UNITS), ordering_identity="plan_or_graph_order"),
        terminal_units=_bounded_items(terminal_units, max(0, min(max_next_units + max_running_units, MAX_NEXT_UNITS + MAX_RUNNING_UNITS)), ordering_identity="plan_or_graph_order"),
        observations=_bounded_items(
            observation_values,
            observation_limit,
            ordering_identity="source_canonical_order",
            presentation_values=observation_presentation,
        ),
        semantic_obligations=_bounded_items(semantic_values, 16, ordering_identity="semantic_owner_order"),
        validation=_bounded_mapping(validation, ("identity", "validation_id", "status", "mutation_identity", "freshness")),
        repository_state=_bounded_mapping(repository_state, ("identity", "source_hash", "freshness", "branch", "status")),
        budgets=_bounded_mapping(selected_budgets, ("model_calls_remaining", "tool_calls_remaining", "input_tokens_remaining", "output_tokens_remaining")),
        recovery=_bounded_mapping(selected_recovery, ("remaining", "used", "scopes")),
        continuity_generation=selected_continuity,
        current_state_id=progress.current_state_id,
        progress_receipt_id=progress.aggregate_progress_id,
        credit_fact_projection=progress.credit_fact_projection[:MAX_CREDIT_PROJECTION],
        credit_fact_total_count=len(progress.credit_fact_ids),
        credit_fact_omitted_count=max(0, len(progress.credit_fact_ids) - len(progress.credit_fact_projection[:MAX_CREDIT_PROJECTION])),
        convergence=selected_convergence,
    )


build_execution_frontier_snapshot = build_execution_frontier


__all__ = [
    "BoundedFrontierCollectionV1",
    "ExecutionFrontierSnapshotV1",
    "FRONTIER_SCHEMA_VERSION",
    "MAX_NEXT_UNITS",
    "MAX_RUNNING_UNITS",
    "MAX_OBSERVATIONS",
    "build_execution_frontier",
    "build_execution_frontier_snapshot",
]

"""Canonical runtime event envelope and bounded diagnostic projection."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping

from agent.runtime.correlation import RunCorrelation
from agent.runtime.event_data import (
    MAX_EVENT_DATA_CHARS,
    MAX_EVENT_DEPTH,
    MAX_EVENT_ITEMS,
    MAX_EVENT_TEXT,
    RESERVED_EVENT_IDENTITY_FIELDS,
    bounded_event_data,
)
from agent.runtime.event_data import (
    event_id as _id,
)
from agent.runtime.event_data import (
    freeze_event_value as _freeze,
)
from agent.runtime.event_data import (
    optional_event_string as _optional_string,
)
from agent.runtime.event_data import (
    unfreeze_event_value as _unfreeze,
)
from agent.runtime.event_kinds import RuntimeEventKind


@dataclass(frozen=True, slots=True)
class RuntimeEvent:
    """Immutable, correlated runtime fact shared by all event sinks."""

    kind: RuntimeEventKind
    run_id: str
    root_task_id: str
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    task_id: str | None = None
    parent_task_id: str | None = None
    node_id: str | None = None
    plan_id: str | None = None
    step_id: str | None = None
    invocation_id: str | None = None
    step: int | None = None
    data: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", RuntimeEventKind.coerce(self.kind))
        object.__setattr__(self, "run_id", _id(self.run_id, "run_id", required=True))
        object.__setattr__(self, "root_task_id", _id(self.root_task_id, "root_task_id", required=True))
        for name in (
            "task_id",
            "parent_task_id",
            "node_id",
            "plan_id",
            "step_id",
            "invocation_id",
        ):
            object.__setattr__(self, name, _id(getattr(self, name), name))
        if self.step is not None and (isinstance(self.step, bool) or not isinstance(self.step, int)):
            raise TypeError("event step must be an integer or null")
        timestamp = self.timestamp.isoformat() if isinstance(self.timestamp, datetime) else str(self.timestamp)
        object.__setattr__(self, "timestamp", timestamp)
        raw_data = self.data if isinstance(self.data, Mapping) else {}
        for name in RESERVED_EVENT_IDENTITY_FIELDS:
            if name in raw_data and raw_data[name] != getattr(self, name):
                raise ValueError(
                    f"event data identity {name} conflicts with the canonical envelope"
                )
        bounded = bounded_event_data(
            {
                str(key): value
                for key, value in raw_data.items()
                if key not in RESERVED_EVENT_IDENTITY_FIELDS
            }
        )
        object.__setattr__(self, "data", _freeze(bounded))

    @classmethod
    def from_fields(
        cls,
        kind: RuntimeEventKind | str,
        correlation: RunCorrelation,
        data: Mapping[str, Any] | None = None,
        *,
        task_id: str | None = None,
        parent_task_id: str | None = None,
        node_id: str | None = None,
        plan_id: str | None = None,
        step_id: str | None = None,
        invocation_id: str | None = None,
        step: int | None = None,
        timestamp: str | datetime | None = None,
    ) -> "RuntimeEvent":
        raw = data if isinstance(data, Mapping) else {}
        selected_step = step if step is not None else raw.get("step")
        selected_timestamp = (
            timestamp.isoformat()
            if isinstance(timestamp, datetime)
            else timestamp or datetime.now(timezone.utc).isoformat()
        )
        return cls(
            kind=RuntimeEventKind.coerce(kind),
            run_id=correlation.run_id,
            root_task_id=correlation.root_task_id,
            timestamp=selected_timestamp,
            task_id=task_id if task_id is not None else correlation.task_id,
            parent_task_id=(
                parent_task_id if parent_task_id is not None else correlation.parent_task_id
            ),
            node_id=node_id if node_id is not None else correlation.node_id,
            plan_id=plan_id if plan_id is not None else _optional_string(raw.get("plan_id")),
            step_id=step_id if step_id is not None else _optional_string(raw.get("step_id")),
            invocation_id=(
                invocation_id
                if invocation_id is not None
                else _optional_string(raw.get("invocation_id"))
            ),
            step=selected_step if isinstance(selected_step, int) and not isinstance(selected_step, bool) else None,
            data=raw,
        )

    @classmethod
    def from_legacy(
        cls,
        event: Mapping[str, Any],
        *,
        correlation: RunCorrelation | None = None,
    ) -> "RuntimeEvent":
        raw_value = event.get("data")
        raw_data: Mapping[str, Any] = raw_value if isinstance(raw_value, Mapping) else {}
        merged_data = dict(raw_data)
        for name in RESERVED_EVENT_IDENTITY_FIELDS:
            if name not in event:
                continue
            value = event.get(name)
            if name in merged_data and merged_data[name] != value:
                raise ValueError(
                    f"legacy event identity {name} conflicts with event data"
                )
            merged_data[name] = value
        run_id = merged_data.get("run_id") or "legacy:unknown"
        root_task_id = merged_data.get("root_task_id") or run_id
        task_id = merged_data.get("task_id")
        if correlation is not None:
            return cls.from_fields(
                str(event.get("type") or "error"),
                correlation,
                merged_data,
                step=event.get("step") if isinstance(event.get("step"), int) else None,
            )
        return cls(
            kind=RuntimeEventKind.coerce(str(event.get("type") or "error")),
            run_id=str(run_id),
            root_task_id=str(root_task_id),
            task_id=str(task_id) if task_id else None,
            parent_task_id=_optional_string(merged_data.get("parent_task_id")),
            node_id=_optional_string(merged_data.get("node_id")),
            plan_id=_optional_string(merged_data.get("plan_id")),
            step_id=_optional_string(merged_data.get("step_id")),
            invocation_id=_optional_string(merged_data.get("invocation_id")),
            step=event.get("step") if isinstance(event.get("step"), int) else None,
            data=merged_data,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.kind.value,
            "step": self.step,
            "run_id": self.run_id,
            "root_task_id": self.root_task_id,
            "task_id": self.task_id,
            "parent_task_id": self.parent_task_id,
            "node_id": self.node_id,
            "plan_id": self.plan_id,
            "step_id": self.step_id,
            "invocation_id": self.invocation_id,
            "timestamp": self.timestamp,
            "data": _unfreeze(self.data),
        }

    def to_legacy_dict(self) -> dict[str, Any]:
        """Serialize through the single state/checkpoint compatibility shape."""

        data = _unfreeze(self.data)
        if not isinstance(data, dict):
            data = {"value": data}
        for name in RESERVED_EVENT_IDENTITY_FIELDS:
            value = getattr(self, name)
            if value is not None:
                data[name] = value
        data = dict(bounded_event_data(data))
        return {
            "type": self.kind.value,
            "step": self.step,
            "data": data,
            "run_id": self.run_id,
            "root_task_id": self.root_task_id,
            "task_id": self.task_id,
            "parent_task_id": self.parent_task_id,
            "node_id": self.node_id,
            "plan_id": self.plan_id,
            "step_id": self.step_id,
            "invocation_id": self.invocation_id,
            "timestamp": self.timestamp,
        }


def serialize_runtime_event(event: RuntimeEvent | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(event, RuntimeEvent):
        return event.to_legacy_dict()
    if isinstance(event, Mapping):
        return RuntimeEvent.from_legacy(event).to_legacy_dict()
    raise TypeError("event must be RuntimeEvent or a legacy event mapping")


def deserialize_runtime_event(
    event: RuntimeEvent | Mapping[str, Any],
    *,
    correlation: RunCorrelation | None = None,
) -> RuntimeEvent:
    if isinstance(event, RuntimeEvent):
        return event
    if isinstance(event, Mapping):
        return RuntimeEvent.from_legacy(event, correlation=correlation)
    raise TypeError("event must be RuntimeEvent or a legacy event mapping")


__all__ = [
    "MAX_EVENT_DATA_CHARS",
    "MAX_EVENT_DEPTH",
    "MAX_EVENT_ITEMS",
    "MAX_EVENT_TEXT",
    "RESERVED_EVENT_IDENTITY_FIELDS",
    "RuntimeEvent",
    "RuntimeEventKind",
    "bounded_event_data",
    "deserialize_runtime_event",
    "serialize_runtime_event",
]

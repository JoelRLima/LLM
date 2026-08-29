"""Canonical runtime-attempt and task-tree correlation identities.

The correlation object links identities owned by the runtime.  It deliberately
does not manufacture plan, step, invocation, changeset, snapshot, or report
identities; those remain owned by their respective domains.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping
from uuid import uuid4


def _new_runtime_id() -> str:
    """Create one runtime identity at the sole correlation boundary."""

    return uuid4().hex


def _required_id(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


@dataclass(frozen=True, slots=True)
class RunCorrelation:
    """Immutable link between one runtime attempt and one task-tree node."""

    run_id: str
    root_task_id: str
    task_id: str
    parent_task_id: str | None = None
    node_id: str | None = None

    def __post_init__(self) -> None:
        run_id = _required_id(self.run_id, "run_id")
        root_task_id = _required_id(self.root_task_id, "root_task_id")
        task_id = _required_id(self.task_id, "task_id")
        object.__setattr__(self, "run_id", run_id)
        object.__setattr__(self, "root_task_id", root_task_id)
        object.__setattr__(self, "task_id", task_id)
        if self.parent_task_id is not None:
            _required_id(self.parent_task_id, "parent_task_id")
            if self.parent_task_id == self.task_id:
                raise ValueError("parent_task_id must differ from task_id")
            if self.task_id == self.root_task_id:
                raise ValueError("a child task_id must differ from root_task_id")
        elif self.task_id != self.root_task_id:
            raise ValueError("a non-root task requires parent_task_id")
        if self.node_id is not None:
            _required_id(self.node_id, "node_id")

    @classmethod
    def fresh(cls, *, root_task_id: str | None = None) -> "RunCorrelation":
        """Create one new attempt and one new logical root task."""

        root = _required_id(root_task_id, "root_task_id") if root_task_id else _new_runtime_id()
        return cls(run_id=_new_runtime_id(), root_task_id=root, task_id=root)

    @classmethod
    def resume(cls, root_task_id: str | None) -> "RunCorrelation":
        """Create a fresh attempt while preserving authoritative root identity."""

        if not root_task_id:
            return cls.fresh()
        root = _required_id(root_task_id, "root_task_id")
        return cls(run_id=_new_runtime_id(), root_task_id=root, task_id=root)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "RunCorrelation | None":
        """Read a compatible serialized correlation without inventing IDs."""

        if not isinstance(value, Mapping):
            return None
        run_id = value.get("run_id")
        root_task_id = value.get("root_task_id")
        task_id = value.get("task_id")
        if not isinstance(run_id, str) or not isinstance(root_task_id, str) or not isinstance(task_id, str):
            return None
        parent_task_id = value.get("parent_task_id")
        node_id = value.get("node_id")
        return cls(
            run_id=run_id,
            root_task_id=root_task_id,
            task_id=task_id,
            parent_task_id=parent_task_id if isinstance(parent_task_id, str) else None,
            node_id=node_id if isinstance(node_id, str) else None,
        )

    def child(self, node_id: str) -> "RunCorrelation":
        """Create a distinct child task linked to this current task."""

        return RunCorrelation(
            run_id=self.run_id,
            root_task_id=self.root_task_id,
            task_id=_new_runtime_id(),
            parent_task_id=self.task_id,
            node_id=_required_id(node_id, "node_id"),
        )

    def unrelated_task(self) -> "RunCorrelation":
        """Start a separate logical task and attempt."""

        return RunCorrelation.fresh()

    def root_context(self) -> "RunCorrelation":
        """Project this attempt back to its logical root context."""

        return RunCorrelation(
            run_id=self.run_id,
            root_task_id=self.root_task_id,
            task_id=self.root_task_id,
        )

    def as_dict(self) -> dict[str, str | None]:
        return {
            "run_id": self.run_id,
            "root_task_id": self.root_task_id,
            "task_id": self.task_id,
            "parent_task_id": self.parent_task_id,
            "node_id": self.node_id,
        }


__all__ = ["RunCorrelation"]

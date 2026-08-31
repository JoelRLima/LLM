"""Compact live/checkpoint binding value objects."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar

from agent.task_definition.contract_model import TaskContract
from agent.task_definition.model_validation import (
    DEFINITION_STATES,
    PHASE_ID_PATTERN,
    TASK_ID_PATTERN,
    digest,
    identifier,
    invalid,
    reject_unknown,
    strict_mapping,
    validate_version,
)
from agent.task_definition.spec_model import TaskSpec


@dataclass(frozen=True, slots=True)
class TaskDefinitionRef:
    """Compact live/checkpoint binding; never contains authority bodies."""

    task_id: str
    contract_version: int
    contract_digest: str
    definition_state: str = "contract_ready"
    spec_version: int | None = None
    spec_digest: str | None = None
    active_phase_id: str | None = None

    _FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "task_id",
            "contract_version",
            "contract_digest",
            "spec_version",
            "spec_digest",
            "active_phase_id",
            "definition_state",
        }
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", identifier(self.task_id, "task_id", TASK_ID_PATTERN))
        object.__setattr__(
            self,
            "contract_version",
            validate_version(self.contract_version, "contract_version"),
        )
        object.__setattr__(self, "contract_digest", digest(self.contract_digest, "contract_digest"))
        if self.definition_state not in DEFINITION_STATES:
            raise invalid("definition_state", "estado não permitido")
        if self.spec_version is not None:
            object.__setattr__(self, "spec_version", validate_version(self.spec_version, "spec_version"))
        if self.spec_digest is not None:
            object.__setattr__(self, "spec_digest", digest(self.spec_digest, "spec_digest"))
        if self.definition_state == "complete":
            if self.spec_version is None or self.spec_digest is None:
                raise invalid("task_definition", "estado complete exige identidade de Spec")
        elif self.spec_version is not None or self.spec_digest is not None:
            raise invalid(
                "task_definition",
                "estado contract_ready não pode conter identidade de Spec",
            )
        if self.definition_state == "contract_ready" and self.active_phase_id is not None:
            raise invalid(
                "active_phase_id",
                "estado contract_ready não pode selecionar uma fase",
            )
        if self.active_phase_id is not None:
            object.__setattr__(
                self,
                "active_phase_id",
                identifier(self.active_phase_id, "active_phase_id", PHASE_ID_PATTERN),
            )

    @property
    def is_complete(self) -> bool:
        return self.definition_state == "complete"

    @classmethod
    def from_dict(cls, value: Any) -> "TaskDefinitionRef":
        mapping = strict_mapping(value, "task_definition")
        reject_unknown(mapping, cls._FIELDS, "task_definition")
        required = ("task_id", "contract_version", "contract_digest", "definition_state")
        if any(field not in mapping for field in required):
            raise invalid("task_definition", "referência incompleta")
        return cls(
            task_id=mapping["task_id"],
            contract_version=mapping["contract_version"],
            contract_digest=mapping["contract_digest"],
            spec_version=mapping.get("spec_version"),
            spec_digest=mapping.get("spec_digest"),
            active_phase_id=mapping.get("active_phase_id"),
            definition_state=mapping["definition_state"],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "contract_version": self.contract_version,
            "contract_digest": self.contract_digest,
            "spec_version": self.spec_version,
            "spec_digest": self.spec_digest,
            "active_phase_id": self.active_phase_id,
            "definition_state": self.definition_state,
        }


TaskDefinitionBinding = TaskDefinitionRef


@dataclass(frozen=True, slots=True)
class TaskDefinitionRecord:
    """Repository load result containing validated bodies and compact binding."""

    contract: TaskContract
    spec: TaskSpec | None
    reference: TaskDefinitionRef
    workspace_id: str

    @property
    def state(self) -> str:
        return self.reference.definition_state


__all__ = [
    "TaskDefinitionBinding",
    "TaskDefinitionRecord",
    "TaskDefinitionRef",
]

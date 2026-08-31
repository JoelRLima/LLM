"""Immutable Contract value object."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar

from agent.task_definition.errors import TaskDefinitionValidationError
from agent.task_definition.model_validation import (
    CONTRACT_VERSION,
    SCHEMA_VERSION,
    TASK_ID_PATTERN,
    identifier,
    reject_unknown,
    strict_mapping,
    text,
    text_collection,
    validate_version,
)


@dataclass(frozen=True, slots=True)
class TaskContract:
    """Stable intent, constraints and completion conditions for one task."""

    task_id: str
    objective: str
    summary: str = ""
    requirements: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    invariants: tuple[str, ...] = ()
    success_criteria: tuple[str, ...] = ()
    out_of_scope: tuple[str, ...] = ()
    stop_conditions: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = ()
    open_questions: tuple[str, ...] = ()
    schema_version: int = SCHEMA_VERSION
    version: int = CONTRACT_VERSION

    _COLLECTION_FIELDS: ClassVar[tuple[str, ...]] = (
        "requirements",
        "constraints",
        "invariants",
        "success_criteria",
        "out_of_scope",
        "stop_conditions",
        "assumptions",
        "open_questions",
    )
    _FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "schema_version",
            "task_id",
            "version",
            "objective",
            "summary",
            "requirements",
            "constraints",
            "invariants",
            "success_criteria",
            "out_of_scope",
            "stop_conditions",
            "assumptions",
            "open_questions",
        }
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", identifier(self.task_id, "task_id", TASK_ID_PATTERN))
        object.__setattr__(self, "objective", text(self.objective, "objective"))
        object.__setattr__(self, "summary", text(self.summary, "summary", allow_empty=True))
        object.__setattr__(self, "schema_version", validate_version(self.schema_version, "schema_version"))
        object.__setattr__(self, "version", validate_version(self.version, "version"))
        for field_name in self._COLLECTION_FIELDS:
            object.__setattr__(
                self,
                field_name,
                text_collection(getattr(self, field_name), field_name),
            )

    @classmethod
    def from_dict(cls, value: Any) -> "TaskContract":
        mapping = strict_mapping(value, "contract")
        reject_unknown(mapping, cls._FIELDS, "contract")
        if "task_id" not in mapping or "objective" not in mapping:
            raise TaskDefinitionValidationError(
                "contract: task_id e objective são obrigatórios"
            )
        return cls(
            task_id=mapping["task_id"],
            objective=mapping["objective"],
            summary=mapping.get("summary", ""),
            requirements=mapping.get("requirements", ()),
            constraints=mapping.get("constraints", ()),
            invariants=mapping.get("invariants", ()),
            success_criteria=mapping.get("success_criteria", ()),
            out_of_scope=mapping.get("out_of_scope", ()),
            stop_conditions=mapping.get("stop_conditions", ()),
            assumptions=mapping.get("assumptions", ()),
            open_questions=mapping.get("open_questions", ()),
            schema_version=mapping.get("schema_version", SCHEMA_VERSION),
            version=mapping.get("version", CONTRACT_VERSION),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "task_id": self.task_id,
            "version": self.version,
            "objective": self.objective,
            "summary": self.summary,
            "requirements": list(self.requirements),
            "constraints": list(self.constraints),
            "invariants": list(self.invariants),
            "success_criteria": list(self.success_criteria),
            "out_of_scope": list(self.out_of_scope),
            "stop_conditions": list(self.stop_conditions),
            "assumptions": list(self.assumptions),
            "open_questions": list(self.open_questions),
        }

    def digest(self) -> str:
        from agent.task_definition.serialization import contract_digest

        return contract_digest(self)


__all__ = ["TaskContract"]

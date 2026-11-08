"""Immutable Contract value object."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar

from llm_agent.agent.task_definition.errors import TaskDefinitionValidationError
from llm_agent.agent.task_definition.model_validation import (
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
from llm_agent.agent.task_definition.verification_model import VerificationCheck


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
    verification_checks: tuple[VerificationCheck, ...] = ()

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
            "verification_checks",
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
        checks = self.verification_checks
        if not isinstance(checks, (tuple, list)) or len(checks) > 64:
            raise TaskDefinitionValidationError("verification_checks: lista inválida ou excede 64")
        checks = tuple(checks)
        if any(not isinstance(check, VerificationCheck) for check in checks):
            raise TaskDefinitionValidationError("verification_checks: exige VerificationCheck")
        if len({check.check_id for check in checks}) != len(checks):
            raise TaskDefinitionValidationError("verification_checks: IDs duplicados")
        for check in checks:
            if check.index >= len(getattr(self, check.field)):
                raise TaskDefinitionValidationError("verification_check: índice fora do Contract")
        object.__setattr__(self, "verification_checks", checks)

    @classmethod
    def from_dict(cls, value: Any) -> "TaskContract":
        mapping = strict_mapping(value, "contract")
        reject_unknown(mapping, cls._FIELDS, "contract")
        raw_checks = mapping.get("verification_checks", ())
        if not isinstance(raw_checks, (list, tuple)):
            raise TaskDefinitionValidationError("verification_checks: deve ser lista")
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
            verification_checks=tuple(
                VerificationCheck.from_dict(item)
                for item in raw_checks
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        result = {
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
        if self.verification_checks:
            result["verification_checks"] = [check.to_dict() for check in self.verification_checks]
        return result

    def digest(self) -> str:
        from llm_agent.agent.task_definition.serialization import contract_digest

        return contract_digest(self)


__all__ = ["TaskContract"]

"""Immutable descriptive Spec and phase value objects."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar

from agent.task_definition.contract_model import TaskContract
from agent.task_definition.errors import TaskDefinitionMismatchError
from agent.task_definition.model_validation import (
    MAX_PHASES,
    PHASE_ID_PATTERN,
    SCHEMA_VERSION,
    SPEC_VERSION,
    TASK_ID_PATTERN,
    digest,
    identifier,
    invalid,
    reject_unknown,
    strict_mapping,
    text,
    text_collection,
    validate_version,
)


@dataclass(frozen=True, slots=True)
class TaskSpecPhase:
    """One immutable, descriptive phase of a task specification."""

    phase_id: str
    title: str
    goal: str
    requirements: tuple[str, ...] = ()
    invariants: tuple[str, ...] = ()
    acceptance_criteria: tuple[str, ...] = ()
    evidence_requirements: tuple[str, ...] = ()
    depends_on: tuple[str, ...] = ()

    _FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "phase_id",
            "title",
            "goal",
            "requirements",
            "invariants",
            "acceptance_criteria",
            "evidence_requirements",
            "depends_on",
        }
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "phase_id", identifier(self.phase_id, "phase_id", PHASE_ID_PATTERN))
        object.__setattr__(self, "title", text(self.title, "title"))
        object.__setattr__(self, "goal", text(self.goal, "goal"))
        for field_name in (
            "requirements",
            "invariants",
            "acceptance_criteria",
            "evidence_requirements",
            "depends_on",
        ):
            object.__setattr__(
                self,
                field_name,
                text_collection(getattr(self, field_name), field_name),
            )
        if len(set(self.depends_on)) != len(self.depends_on):
            raise invalid("depends_on", "não pode conter dependências duplicadas")

    @classmethod
    def from_dict(cls, value: Any) -> "TaskSpecPhase":
        mapping = strict_mapping(value, "phase")
        reject_unknown(mapping, cls._FIELDS, "phase")
        if any(field not in mapping for field in ("phase_id", "title", "goal")):
            raise invalid("phase", "phase_id, title e goal são obrigatórios")
        return cls(
            phase_id=mapping["phase_id"],
            title=mapping["title"],
            goal=mapping["goal"],
            requirements=mapping.get("requirements", ()),
            invariants=mapping.get("invariants", ()),
            acceptance_criteria=mapping.get("acceptance_criteria", ()),
            evidence_requirements=mapping.get("evidence_requirements", ()),
            depends_on=mapping.get("depends_on", ()),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase_id": self.phase_id,
            "title": self.title,
            "goal": self.goal,
            "requirements": list(self.requirements),
            "invariants": list(self.invariants),
            "acceptance_criteria": list(self.acceptance_criteria),
            "evidence_requirements": list(self.evidence_requirements),
            "depends_on": list(self.depends_on),
        }


@dataclass(frozen=True, slots=True)
class TaskSpec:
    """Immutable structured requirements bound to one exact Contract."""

    task_id: str
    contract_version: int
    contract_digest: str
    phases: tuple[TaskSpecPhase, ...]
    architecture: str = ""
    global_requirements: tuple[str, ...] = ()
    global_invariants: tuple[str, ...] = ()
    global_acceptance: tuple[str, ...] = ()
    schema_version: int = SCHEMA_VERSION
    version: int = SPEC_VERSION

    _FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "schema_version",
            "task_id",
            "version",
            "contract_version",
            "contract_digest",
            "architecture",
            "global_requirements",
            "global_invariants",
            "global_acceptance",
            "phases",
        }
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", identifier(self.task_id, "task_id", TASK_ID_PATTERN))
        object.__setattr__(self, "schema_version", validate_version(self.schema_version, "schema_version"))
        object.__setattr__(self, "version", validate_version(self.version, "version"))
        object.__setattr__(
            self,
            "contract_version",
            validate_version(self.contract_version, "contract_version"),
        )
        object.__setattr__(self, "contract_digest", digest(self.contract_digest, "contract_digest"))
        object.__setattr__(self, "architecture", text(self.architecture, "architecture", allow_empty=True))
        for field_name in ("global_requirements", "global_invariants", "global_acceptance"):
            object.__setattr__(
                self,
                field_name,
                text_collection(getattr(self, field_name), field_name),
            )
        self._normalize_phases()
        self._validate_phase_graph()

    def _normalize_phases(self) -> None:
        if not isinstance(self.phases, (list, tuple)):
            raise invalid("phases", "deve ser uma lista/tupla de fases")
        if not 1 <= len(self.phases) <= MAX_PHASES:
            raise invalid("phases", f"deve conter entre 1 e {MAX_PHASES} fases")
        normalized: list[TaskSpecPhase] = []
        for index, phase in enumerate(self.phases):
            if not isinstance(phase, TaskSpecPhase):
                raise invalid(f"phases[{index}]", "não é uma TaskSpecPhase admitida")
            normalized.append(phase)
        object.__setattr__(self, "phases", tuple(normalized))

    def _validate_phase_graph(self) -> None:
        phase_ids = [phase.phase_id for phase in self.phases]
        if len(set(phase_ids)) != len(phase_ids):
            raise invalid("phases", "phase_id duplicado")
        known = set(phase_ids)
        for phase in self.phases:
            missing = [item for item in phase.depends_on if item not in known]
            if missing:
                raise invalid(
                    f"phases.{phase.phase_id}.depends_on",
                    f"fase inexistente: {missing[0]}",
                )
            if phase.phase_id in phase.depends_on:
                raise invalid(
                    f"phases.{phase.phase_id}.depends_on",
                    "auto-dependência não permitida",
                )
        self._validate_acyclic()

    def _validate_acyclic(self) -> None:
        dependencies = {phase.phase_id: set(phase.depends_on) for phase in self.phases}
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(phase_id: str) -> None:
            if phase_id in visiting:
                raise invalid("phases", "grafo de dependências contém ciclo")
            if phase_id in visited:
                return
            visiting.add(phase_id)
            for dependency in dependencies[phase_id]:
                visit(dependency)
            visiting.remove(phase_id)
            visited.add(phase_id)

        for phase in self.phases:
            visit(phase.phase_id)

    @classmethod
    def from_dict(cls, value: Any) -> "TaskSpec":
        mapping = strict_mapping(value, "spec")
        reject_unknown(mapping, cls._FIELDS, "spec")
        required = ("task_id", "contract_version", "contract_digest", "phases")
        if any(field not in mapping for field in required):
            raise invalid(
                "spec",
                "task_id, contract_version, contract_digest e phases são obrigatórios",
            )
        raw_phases = mapping["phases"]
        if not isinstance(raw_phases, (list, tuple)):
            raise invalid("spec.phases", "deve ser uma lista")
        return cls(
            task_id=mapping["task_id"],
            contract_version=mapping["contract_version"],
            contract_digest=mapping["contract_digest"],
            phases=tuple(TaskSpecPhase.from_dict(item) for item in raw_phases),
            architecture=mapping.get("architecture", ""),
            global_requirements=mapping.get("global_requirements", ()),
            global_invariants=mapping.get("global_invariants", ()),
            global_acceptance=mapping.get("global_acceptance", ()),
            schema_version=mapping.get("schema_version", SCHEMA_VERSION),
            version=mapping.get("version", SPEC_VERSION),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "task_id": self.task_id,
            "version": self.version,
            "contract_version": self.contract_version,
            "contract_digest": self.contract_digest,
            "architecture": self.architecture,
            "global_requirements": list(self.global_requirements),
            "global_invariants": list(self.global_invariants),
            "global_acceptance": list(self.global_acceptance),
            "phases": [phase.to_dict() for phase in self.phases],
        }

    def validate_against(self, contract: TaskContract) -> None:
        if self.task_id != contract.task_id:
            raise TaskDefinitionMismatchError(
                f"Spec task_id '{self.task_id}' não corresponde ao Contract '{contract.task_id}'."
            )
        if self.contract_version != contract.version:
            raise TaskDefinitionMismatchError(
                f"Spec contract_version {self.contract_version} não corresponde ao Contract {contract.version}."
            )
        if self.contract_digest != contract.digest():
            raise TaskDefinitionMismatchError(
                "Spec contract_digest não corresponde ao Contract persistido."
            )

    def digest(self) -> str:
        from agent.task_definition.serialization import spec_digest

        return spec_digest(self)


__all__ = ["TaskSpec", "TaskSpecPhase"]

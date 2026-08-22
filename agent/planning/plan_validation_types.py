"""Public value types returned by the plan validator."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


@dataclass(frozen=True)
class BlockedStep:
    """Um passo do plano que não pode ser executado como está."""

    index: int
    reason: str
    repairable_fields: frozenset[str] = frozenset()

    @property
    def is_validation_repair(self) -> bool:
        """Whether this is a deterministic, field-scoped pre-execution repair."""

        return bool(self.repairable_fields)


@dataclass
class ValidationReport:
    """Resultado de uma chamada a `PlanValidator.validate()`."""

    is_valid: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    blocked_steps: List[BlockedStep] = field(default_factory=list)


__all__ = ["BlockedStep", "ValidationReport"]

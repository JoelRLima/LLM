"""Shared value types for deterministic interaction guards."""

from dataclasses import dataclass
from enum import Enum


class OperationalClassification(str, Enum):
    DIRECT = "DIRECT"
    NEGATED = "NEGATED"
    HYPOTHETICAL = "HYPOTHETICAL"
    QUOTED = "QUOTED"
    META = "META"
    CONTEXTUAL = "CONTEXTUAL"
    CONFLICT = "CONFLICT"
    UNKNOWN = "UNKNOWN"


class TargetProof(str, Enum):
    PROVEN = "PROVEN"
    UNPROVEN = "UNPROVEN"


class ReadClassification(str, Enum):
    DIRECT_READ = "DIRECT_READ"
    CONTEXTUAL = "CONTEXTUAL"
    OPERATIONAL = "OPERATIONAL"
    PROPOSAL = "PROPOSAL"
    META = "META"
    UNKNOWN = "UNKNOWN"


class PlanClassification(str, Enum):
    DIRECT_PLAN = "DIRECT_PLAN"
    CONTEXTUAL = "CONTEXTUAL"
    OPERATIONAL = "OPERATIONAL"
    META = "META"
    UNKNOWN = "UNKNOWN"


class LocalConflictClassification(str, Enum):
    CLEAR = "CLEAR"
    CONFLICT = "CONFLICT"


class MixedIntentClassification(str, Enum):
    CLEAR = "CLEAR"
    MIXED_EFFECT = "MIXED_EFFECT"


class CrossClauseRelation(str, Enum):
    CLEAR = "CLEAR"
    INDEPENDENT = "INDEPENDENT"
    FAMILY_CONFLICT = "FAMILY_CONFLICT"
    SAME_TARGET_CONFLICT = "SAME_TARGET_CONFLICT"
    GLOBAL_CONFLICT = "GLOBAL_CONFLICT"
    UNKNOWN_RELATION_CONFLICT = "UNKNOWN_RELATION_CONFLICT"


@dataclass(frozen=True, slots=True)
class TargetAnchorIdentity:
    identity_class: str
    canonical: str | None
    identity_unresolved: bool = False

    @property
    def kind(self) -> str:
        return self.identity_class

    @property
    def identity(self) -> str | None:
        return self.canonical

    @property
    def normalized(self) -> str | None:
        return self.canonical


@dataclass(frozen=True, slots=True)
class OperationalAnalysis:
    classification: OperationalClassification
    family: str | None = None
    predicate: str | None = None
    complement: str = ""
    predicate_start: int = -1


@dataclass(frozen=True, slots=True)
class NegativeRestriction:
    family: str
    scope: str
    complement: str


_CONTEXTUAL_COMPLEMENT_PREFIXES = (
    "isso",
    "isto",
    "esse",
    "essa",
    "este",
    "esta",
    "aquilo",
    "this",
    "that",
    "it",
    "the previous one",
    "esse arquivo",
    "essa pasta",
    "esse m??dulo",
    "esse modulo",
    "essa m??dulo",
    "essa modulo",
    "this file",
    "that file",
    "this module",
    "that module",
    "this repository",
    "that repository",
    "this project",
    "that project",
)


def _is_contextual_complement(complement: str) -> bool:
    value = complement.strip()
    return any(value == prefix or value.startswith(prefix + " ") for prefix in _CONTEXTUAL_COMPLEMENT_PREFIXES)

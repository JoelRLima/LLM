"""Verification result and runtime-authored seal contracts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any

from agent.code.outcome_proposal import CodeProposalKind
from agent.resources.contracts import normalize_resource_id


class CodeOutcomeVerdict(str, Enum):
    SUPPORTED = "SUPPORTED"
    CONTRADICTED = "CONTRADICTED"
    INSUFFICIENT = "INSUFFICIENT"


@dataclass(frozen=True, slots=True)
class CodeOutcomeVerification:
    verdict: CodeOutcomeVerdict
    reason: str
    evidence_ids: tuple[str, ...] = ()
    failure_code: str | None = None
    stale: bool = False


@dataclass(frozen=True, slots=True)
class CodeOutcomeSeal:
    """Runtime-authored seal; it is never accepted from model JSON."""

    kind: str
    verification: str
    evidence_manifest_id: str
    evidence_current: bool
    verified_resources: tuple[str, ...]
    cited_evidence_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.kind != CodeProposalKind.NO_CHANGE.value:
            raise ValueError("seal kind invÃ¡lido")
        if self.verification != CodeOutcomeVerdict.SUPPORTED.value:
            raise ValueError("seal verification invÃ¡lida")
        if (
            not isinstance(self.evidence_manifest_id, str)
            or not self.evidence_manifest_id.strip()
            or type(self.evidence_current) is not bool
            or not self.evidence_current
        ):
            raise ValueError("seal exige manifesto atual")
        if (
            not self.verified_resources
            or not self.cited_evidence_ids
            or any(type(item) is not str or not item.strip() for item in self.verified_resources)
            or any(type(item) is not str or not item.strip() for item in self.cited_evidence_ids)
            or len(set(self.verified_resources)) != len(self.verified_resources)
            or len(set(self.cited_evidence_ids)) != len(self.cited_evidence_ids)
            or any(normalize_resource_id(item) == "*" for item in self.verified_resources)
            or len(
                {normalize_resource_id(item) for item in self.verified_resources}
            )
            != len(self.verified_resources)
        ):
            raise ValueError("seal exige recursos e evidÃªncias")

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "verification": self.verification,
            "evidence_manifest_id": self.evidence_manifest_id,
            "evidence_current": self.evidence_current,
            "verified_resources": list(self.verified_resources),
            "cited_evidence_ids": list(self.cited_evidence_ids),
        }

    @classmethod
    def from_dict(cls, value: Any) -> "CodeOutcomeSeal":
        """Parse only the closed runtime seal shape."""

        if not isinstance(value, Mapping):
            raise ValueError("code outcome seal invalido")
        expected = {
            "kind", "verification", "evidence_manifest_id", "evidence_current",
            "verified_resources", "cited_evidence_ids",
        }
        if set(value) != expected:
            raise ValueError("code outcome seal contem campos invalidos")
        resources = value["verified_resources"]
        cited = value["cited_evidence_ids"]
        if not isinstance(resources, list) or not isinstance(cited, list):
            raise ValueError("escopo do code outcome seal invalido")
        if any(type(item) is not str for item in (*resources, *cited)):
            raise ValueError("identidade do code outcome seal invalida")
        kind = value["kind"]
        verification = value["verification"]
        manifest_id = value["evidence_manifest_id"]
        current = value["evidence_current"]
        if not isinstance(kind, str) or not isinstance(verification, str):
            raise ValueError("identidade do code outcome seal invalida")
        if not isinstance(manifest_id, str) or not isinstance(current, bool):
            raise ValueError("estado do code outcome seal invalido")
        return cls(
            kind=kind,
            verification=verification,
            evidence_manifest_id=manifest_id,
            evidence_current=current,
            verified_resources=tuple(resources),
            cited_evidence_ids=tuple(cited),
        )


__all__ = ["CodeOutcomeSeal", "CodeOutcomeVerification", "CodeOutcomeVerdict"]

"""Stable references into immutable task definitions, without completion claims."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

CONTRACT_FIELDS = (
    "requirements", "constraints", "invariants", "success_criteria", "out_of_scope", "stop_conditions",
)
SPEC_FIELDS = ("global_requirements", "global_invariants", "global_acceptance")
PHASE_FIELDS = ("requirements", "invariants", "acceptance_criteria", "evidence_requirements")


@dataclass(frozen=True, slots=True)
class RequirementReference:
    """An address in one exact definition; it grants no effect or evidence."""

    requirement_id: str
    definition_digest: str
    source: str
    index: int
    phase_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "requirement_id": self.requirement_id,
            "definition_digest": self.definition_digest,
            "source": self.source,
            "index": self.index,
            "phase_id": self.phase_id,
        }


def _references(
    owner: Mapping[str, Any],
    fields: Sequence[str],
    *,
    digest: str,
    source: str,
    phase_id: str | None = None,
) -> tuple[RequirementReference, ...]:
    references: list[RequirementReference] = []
    for name in fields:
        for index, _item in enumerate(owner.get(name, ())):
            address = f"{source}.{name}"
            identity = json.dumps(
                ("task_requirement.v1", digest, address, phase_id, index), separators=(",", ":"),
            )
            requirement_id = "req-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()
            references.append(RequirementReference(requirement_id, digest, address, index, phase_id))
    return tuple(references)


def requirement_references(authority: Mapping[str, Any]) -> tuple[RequirementReference, ...]:
    """Index the visible trusted projection, preserving whole/selected-phase IDs."""
    contract = authority["contract"]
    spec = authority["spec"]
    references = list(_references(contract, CONTRACT_FIELDS, digest=contract["digest"], source="contract"))
    references.extend(_references(spec, SPEC_FIELDS, digest=spec["digest"], source="spec"))
    phases = list(spec.get("phases", ()))
    if "selected_phase" in authority:
        phases.append(authority["selected_phase"])
        phases.extend(authority.get("dependency_facts", ()))
    for phase in phases:
        references.extend(_references(
            phase, PHASE_FIELDS, digest=spec["digest"], source="phase", phase_id=phase["phase_id"],
        ))
    return tuple(references)


__all__ = ["RequirementReference", "requirement_references"]

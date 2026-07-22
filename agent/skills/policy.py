"""Autorização de skills por capacidades, separada de personas e prompts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable

from agent.skills.catalog import BUILTIN_SKILL_SPECS
from agent.skills.descriptor import SkillCapability, SkillDescriptor


@dataclass(frozen=True)
class CapabilityPolicy:
    allowed: frozenset[SkillCapability]

    def authorize(self, descriptor: SkillDescriptor) -> bool:
        return descriptor.spec.capabilities.issubset(self.allowed)


PERSONA_CAPABILITIES: Dict[str, frozenset[SkillCapability]] = {
    "coder": frozenset(
        {
            SkillCapability.READ,
            SkillCapability.WRITE,
            SkillCapability.PROCESS,
            SkillCapability.MEMORY,
            SkillCapability.ANALYZE,
            SkillCapability.VCS_READ,
        }
    ),
    "researcher": frozenset(
        {SkillCapability.NETWORK, SkillCapability.MEMORY, SkillCapability.ANALYZE}
    ),
    "security_auditor": frozenset(
        {
            SkillCapability.READ,
            SkillCapability.PROCESS,
            SkillCapability.NETWORK,
            SkillCapability.ANALYZE,
            SkillCapability.VCS_READ,
        }
    ),
    "general": frozenset(
        {
            SkillCapability.READ,
            SkillCapability.WRITE,
            SkillCapability.PROCESS,
            SkillCapability.MEMORY,
            SkillCapability.ANALYZE,
        }
    ),
}


def builtin_skills_for_persona(persona: str) -> list[str]:
    allowed = PERSONA_CAPABILITIES.get(persona, PERSONA_CAPABILITIES["general"])
    return [
        spec.name
        for spec in BUILTIN_SKILL_SPECS
        if spec.capabilities.issubset(allowed)
    ]


def denied_capabilities(
    descriptor: SkillDescriptor,
    allowed: Iterable[SkillCapability],
) -> frozenset[SkillCapability]:
    return descriptor.spec.capabilities - frozenset(allowed)

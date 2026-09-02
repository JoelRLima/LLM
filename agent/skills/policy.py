"""Autorização de skills por capacidades, separada de personas e prompts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable

from agent.skills.catalog import BUILTIN_SKILL_SPECS
from agent.skills.descriptor import SkillCapability, SkillDescriptor
from agent.tools.contracts import ToolOriginKind

# ``file_writer`` remains registered for explicit low-level/admin callers, but
# is not a model-actionable planner tool. Supported product modifications go
# through ``code_task`` so ChangeSet approval and ProjectValidator cannot be
# bypassed by a direct write plan.
MODEL_ACTIONABLE_EXCLUDED = frozenset({"file_writer"})


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
            SkillCapability.VALIDATE,
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
            SkillCapability.VALIDATE,
        }
    ),
}


def builtin_skills_for_persona(persona: str, registry: Any = None) -> list[str]:
    allowed = PERSONA_CAPABILITIES.get(persona, PERSONA_CAPABILITIES["general"])
    allowed_values = {c.value if hasattr(c, "value") else str(c) for c in allowed}
    if registry is not None and hasattr(registry, "descriptors") and callable(registry.descriptors):
        results: list[str] = []
        for desc in registry.descriptors():
            if desc.name in MODEL_ACTIONABLE_EXCLUDED:
                continue
            if getattr(desc, "origin_kind", ToolOriginKind.BUILTIN) is not ToolOriginKind.BUILTIN:
                continue
            caps = {c.value if hasattr(c, "value") else str(c) for c in desc.capabilities}
            if caps.issubset(allowed_values):
                results.append(desc.name)
        return results

    return [
        spec.name
        for spec in BUILTIN_SKILL_SPECS
        if spec.name not in MODEL_ACTIONABLE_EXCLUDED
        if spec.capabilities.issubset(allowed)
    ]


def project_eligible_extension_descriptors(
    active: list[str], skills: dict[str, Any], context: Any, registry: Any
) -> None:
    """Project admitted extension descriptors into the planning tool view."""
    if context is None or registry is None:
        return
    for tool in context.tools:
        if getattr(getattr(tool, "origin_kind", None), "value", None) != "extension":
            continue
        if tool.name not in active:
            active.append(tool.name)
        if tool.name not in skills:
            try:
                skills[tool.name] = registry.descriptor(tool.name)
            except KeyError:
                pass


def persona_allowed_capabilities(persona: str) -> frozenset[str]:
    allowed = PERSONA_CAPABILITIES.get(persona, PERSONA_CAPABILITIES["general"])
    return frozenset(c.value if hasattr(c, "value") else str(c) for c in allowed)


def denied_capabilities(
    descriptor: SkillDescriptor,
    allowed: Iterable[SkillCapability],
) -> frozenset[SkillCapability]:
    return descriptor.spec.capabilities - frozenset(allowed)

from pathlib import Path

import pytest

from agent.skills import load_all_skills, load_skill_registry
from agent.skills.descriptor import SkillCapability, SkillDescriptor, SkillSpec
from agent.skills.policy import CapabilityPolicy, builtin_skills_for_persona
from agent.skills.registry import SkillRegistry


class EchoLike:
    name = "echo_like"
    description = "echo"

    def get_schema(self):
        return {}

    def execute(self, args):
        return {"ok": True, "data": args}


def test_builtin_registry_is_canonical_and_uses_actual_skill_names(tmp_path: Path):
    registry = load_skill_registry(base_dir=tmp_path)

    assert "git_reader" in registry.names()
    assert "git" not in registry.names()
    assert registry.descriptor("file_writer").spec.cost == 8
    assert SkillCapability.WRITE in registry.descriptor("file_writer").spec.capabilities
    assert set(skill.name for skill in load_all_skills(base_dir=tmp_path)) == set(registry.names())


def test_registry_rejects_duplicates_and_spec_name_mismatch():
    spec = SkillSpec("tests.fake", "EchoLike", "echo_like")
    descriptor = SkillDescriptor(spec=spec, skill=EchoLike())
    registry = SkillRegistry()
    registry.register(descriptor)

    with pytest.raises(ValueError, match="duplicada"):
        registry.register(descriptor)

    with pytest.raises(ValueError, match="diverge"):
        SkillRegistry().register(
            SkillDescriptor(
                spec=SkillSpec("tests.fake", "EchoLike", "other"),
                skill=EchoLike(),
            )
        )


def test_capability_policy_denies_ungranted_side_effects(tmp_path: Path):
    registry = load_skill_registry(base_dir=tmp_path)
    read_only = CapabilityPolicy(
        frozenset({SkillCapability.READ, SkillCapability.ANALYZE})
    )

    assert read_only.authorize(registry.descriptor("code_analyzer")) is True
    assert read_only.authorize(registry.descriptor("file_writer")) is False


def test_persona_tools_are_derived_from_capabilities():
    researcher = builtin_skills_for_persona("researcher")
    security = builtin_skills_for_persona("security_auditor")

    assert {"web_search", "summarize", "session_memory"}.issubset(researcher)
    assert "file_writer" not in researcher
    assert "file_writer" not in security

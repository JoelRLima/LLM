from pathlib import Path

from agent.planning.plan_validator import PlanValidator
from agent.skills import load_skill_registry
from agent.skills.policy import persona_allowed_capabilities
from agent.tools.builtin_adapter import BuiltinToolAdapter
from agent.tools.tool_registry import ToolRegistry


def test_plan_validator_blocks_tool_with_disallowed_capabilities(tmp_path: Path) -> None:
    skill_reg = load_skill_registry(base_dir=tmp_path)
    registry = ToolRegistry()
    registry.register_adapter(BuiltinToolAdapter(skill_reg))

    allowed_capabilities = persona_allowed_capabilities("researcher")
    validator = PlanValidator(
        skills={name: skill_reg.skill(name) for name in skill_reg.names()},
        active_skills=[],
        allowed_capabilities=allowed_capabilities,
        tool_registry=registry,
    )

    report = validator.validate([
        {"tool": "file_writer", "args": {"file_path": "example.txt", "content": "text"}},
    ])

    assert not report.is_valid
    assert any("capacidades não autorizadas" in blocked.reason for blocked in report.blocked_steps)

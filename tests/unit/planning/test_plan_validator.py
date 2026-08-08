from pathlib import Path
from types import SimpleNamespace

from agent.planning.plan_validator import PlanValidator
from agent.planning.planning_context import PlanningContextSnapshot, PlanningTool
from agent.skills import load_skill_registry
from agent.skills.policy import persona_allowed_capabilities
from agent.tools.builtin_adapter import BuiltinToolAdapter
from agent.tools.contracts import ToolOriginKind
from agent.tools.runtime_identity import RuntimeSnapshotIdentity
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
    assert any("capacidades" in blocked.reason for blocked in report.blocked_steps)


def test_context_validator_rejects_tool_not_in_presented_projection() -> None:
    context = PlanningContextSnapshot(
        snapshot_id="ctx-1",
        registry_identity="registry-1",
        authority_identity="authority-1",
        tools=(
            PlanningTool(name="builtin", description="builtin"),
            PlanningTool(
                name="external",
                description="external",
                required_capabilities=frozenset({"read"}),
                origin_kind=ToolOriginKind.EXTENSION,
                extension_id="scanner.extension",
            ),
        ),
        eligible_names=frozenset({"builtin", "external"}),
        runtime_identity=RuntimeSnapshotIdentity("registry-1", "workspace"),
    )
    validator = PlanValidator(
        skills={},
        active_skills=[],
        allowed_capabilities=frozenset({"read"}),
        planning_context=context,
        presented_names=frozenset({"builtin"}),
    )
    report = validator.validate([{"tool": "external", "args": {}}])
    assert not report.is_valid
    assert "apresentada" in report.blocked_steps[0].reason


def test_context_validator_uses_required_capabilities_from_canonical_tool() -> None:
    context = PlanningContextSnapshot(
        snapshot_id="ctx-cap",
        registry_identity="registry-cap",
        authority_identity="authority-cap",
        tools=(
            PlanningTool(
                name="write_tool",
                description="write",
                required_capabilities=frozenset({"write"}),
            ),
        ),
        eligible_names=frozenset({"write_tool"}),
        runtime_identity=RuntimeSnapshotIdentity("registry-cap", "workspace"),
        allowed_capabilities=frozenset({"read"}),
    )
    validator = PlanValidator(
        skills={},
        planning_context=context,
        presented_names=frozenset({"write_tool"}),
        allowed_capabilities=frozenset({"read"}),
    )
    report = validator.validate([{"tool": "write_tool", "args": {}}])
    assert not report.is_valid
    assert "capacidades" in report.blocked_steps[0].reason


def test_context_validator_ignores_conflicting_legacy_schema() -> None:
    context = PlanningContextSnapshot(
        snapshot_id="ctx-schema",
        registry_identity="registry-schema",
        authority_identity="authority-schema",
        tools=(PlanningTool(name="canonical", description="canonical"),),
        eligible_names=frozenset({"canonical"}),
        runtime_identity=RuntimeSnapshotIdentity("registry-schema", "workspace"),
    )
    legacy = type("Legacy", (), {"get_schema": lambda self: {"required": ["legacy_only"]}})()
    report = PlanValidator(
        skills={"canonical": legacy},
        planning_context=context,
        presented_names=frozenset({"canonical"}),
    ).validate([{"tool": "canonical", "args": {}}])
    assert report.is_valid


def test_legacy_descriptor_malformed_schema_is_blocked_without_raw_exception() -> None:
    descriptor = SimpleNamespace(name="legacy", schema={"properties": []})
    registry = SimpleNamespace(descriptor=lambda name: descriptor)
    report = PlanValidator(
        skills={}, active_skills=[], tool_registry=registry
    ).validate([{"tool": "legacy", "args": {"x": "value"}}])
    assert not report.is_valid
    assert "Schema inválido" in report.blocked_steps[0].reason

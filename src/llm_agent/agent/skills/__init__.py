"""API pública do catálogo de skills."""

from pathlib import Path
from typing import Any, List, cast

from llm_agent.agent.runtime.logging import logger

from .base import BaseSkill
from .registry import SkillRegistry, build_builtin_registry


def load_skill_registry(
    base_dir: str | Path = ".",
    scratch_dir: str | Path | None = None,
    orchestrator: Any = None,
    model_gateway: Any = None,
    config: Any = None,
    approval_policy: Any = None,
    *,
    session: Any = None,
    memory: Any = None,
    workspace_manager: Any = None,
) -> SkillRegistry:
    """Constrói o registro embutido com dependências explícitas."""

    try:
        return build_builtin_registry(
            base_dir=base_dir,
            scratch_dir=scratch_dir,
            session=session,
            memory=memory,
            workspace_manager=workspace_manager,
            orchestrator=orchestrator,
            model_gateway=model_gateway,
            config=config,
            approval_policy=approval_policy,
        )
    except Exception as exc:
        logger.error(f"Falha ao construir o registro de skills: {exc}")
        raise


def load_all_skills(
    base_dir: str | Path = ".",
    scratch_dir: str | Path | None = None,
    orchestrator: Any = None,
    model_gateway: Any = None,
    config: Any = None,
    approval_policy: Any = None,
    *,
    session: Any = None,
    memory: Any = None,
    workspace_manager: Any = None,
) -> List[BaseSkill]:
    """Return the canonical registry contents as an ordered skill collection."""

    registry = load_skill_registry(
        base_dir=base_dir,
        scratch_dir=scratch_dir,
        session=session,
        memory=memory,
        workspace_manager=workspace_manager,
        orchestrator=orchestrator,
        model_gateway=model_gateway,
        config=config,
        approval_policy=approval_policy,
    )
    return [cast(BaseSkill, skill) for skill in registry.skills()]


def load_tool_registry(
    base_dir: str | Path = ".",
    scratch_dir: str | Path | None = None,
    orchestrator: Any = None,
    model_gateway: Any = None,
    config: Any = None,
    approval_policy: Any = None,
    extensions_state_path: str | Path | None = None,
    skill_registry: SkillRegistry | None = None,
    *,
    session: Any = None,
    memory: Any = None,
    workspace_manager: Any = None,
) -> Any:
    """Constrói o ToolRegistry populado com as ferramentas builtin e extensões habilitadas."""
    from llm_agent.agent.tools.builtin_adapter import BuiltinToolAdapter
    from llm_agent.agent.tools.stdio_adapter import StdioToolAdapter, load_strict_extension_manifest
    from llm_agent.agent.tools.tool_registry import ToolRegistry
    from llm_agent.extensions.extension_registry import ExtensionRegistry

    skill_reg = skill_registry or load_skill_registry(
        base_dir=base_dir,
        scratch_dir=scratch_dir,
        session=session,
        memory=memory,
        workspace_manager=workspace_manager,
        orchestrator=orchestrator,
        model_gateway=model_gateway,
        config=config,
        approval_policy=approval_policy,
    )
    tool_reg = ToolRegistry()
    tool_reg.register_adapter(BuiltinToolAdapter(skill_reg))

    if extensions_state_path is None:
        return tool_reg

    registry = ExtensionRegistry(extensions_state_path)
    for entry in registry.list():
        if not entry.enabled:
            continue
        manifest = load_strict_extension_manifest(entry.manifest_path)
        tool_reg.register_adapter(StdioToolAdapter(manifest, cwd=base_dir))
    return tool_reg


__all__ = ["BaseSkill", "SkillRegistry", "load_all_skills", "load_skill_registry", "load_tool_registry"]

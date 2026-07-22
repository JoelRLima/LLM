"""API pública do catálogo de skills."""

from pathlib import Path
from typing import Any, List, cast

from agent.runtime.logging import logger

from .base import BaseSkill
from .registry import SkillRegistry, build_builtin_registry


def load_skill_registry(
    base_dir: str | Path = ".",
    orchestrator: Any = None,
    model_gateway: Any = None,
    config: Any = None,
) -> SkillRegistry:
    """Constrói o registro embutido com dependências explícitas."""

    try:
        return build_builtin_registry(
            base_dir=base_dir,
            orchestrator=orchestrator,
            model_gateway=model_gateway,
            config=config,
        )
    except Exception as exc:
        logger.error(f"Falha ao construir o registro de skills: {exc}")
        raise


def load_all_skills(
    base_dir: str | Path = ".",
    orchestrator: Any = None,
    model_gateway: Any = None,
    config: Any = None,
) -> List[BaseSkill]:
    """Fachada compatível; código novo deve preferir `load_skill_registry`."""

    registry = load_skill_registry(
        base_dir=base_dir,
        orchestrator=orchestrator,
        model_gateway=model_gateway,
        config=config,
    )
    return [cast(BaseSkill, skill) for skill in registry.skills()]


__all__ = ["BaseSkill", "SkillRegistry", "load_all_skills", "load_skill_registry"]

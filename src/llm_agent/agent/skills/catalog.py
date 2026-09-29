"""Catálogo único das skills embutidas.

Adicionar uma skill interna requer um `SkillSpec` aqui e a implementação da
classe. Construção, custo, capacidade e timeout não vivem mais em mapas
independentes.
"""
from __future__ import annotations

from llm_agent.agent.operation.catalog import BUILTIN_SKILL_SPECS, BUILTIN_SPEC_BY_NAME

__all__ = ["BUILTIN_SKILL_SPECS", "BUILTIN_SPEC_BY_NAME"]

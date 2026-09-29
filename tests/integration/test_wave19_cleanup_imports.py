from __future__ import annotations

import importlib


def test_current_w19_surfaces_import_without_retired_facades() -> None:
    modules = (
        "llm_agent.agent.application",
        "llm_agent.application.services.queries",
        "llm_agent.interfaces.cli.action_registry",
        "llm_agent.interfaces.cli.action_parser",
        "llm_agent.interfaces.cli.commands",
        "llm_agent.interfaces.cli.interactive_resources",
        "llm_agent.interfaces.cli.query_executor",
        "llm_agent.interfaces.cli.output_projection",
        "llm_agent.interfaces.cli.output_viewer",
        "llm_agent.agent.routing.persona.current",
        "llm_agent.agent.evaluation.experiment",
        "llm_agent.agent.evaluation.receipt",
        "llm_agent.agent.evaluation.comparison",
        "llm_agent.agent.evaluation.feedback",
        "llm_agent.outputs.service",
    )
    for module in modules:
        assert importlib.import_module(module) is not None


def test_retired_facades_are_not_importable() -> None:
    for module in (
        "llm_agent.agent.llm.router",
        "llm_agent.interfaces.cli.manifest",
        "llm_agent.interfaces.cli.query_plane",
        "llm_agent.interfaces.cli.query_find",
        "llm_agent.interfaces.cli.query_git",
    ):
        try:
            importlib.import_module(module)
        except ModuleNotFoundError:
            continue
        raise AssertionError(f"retired module remains importable: {module}")

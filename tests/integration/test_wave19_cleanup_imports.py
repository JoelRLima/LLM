from __future__ import annotations

import importlib


def test_current_w19_surfaces_import_without_retired_facades() -> None:
    modules = (
        "agent.application",
        "agent.application_services.queries",
        "agent.interfaces.cli.action_registry",
        "agent.interfaces.cli.action_parser",
        "agent.interfaces.cli.commands",
        "agent.interfaces.cli.interactive_resources",
        "agent.interfaces.cli.query_executor",
        "agent.interfaces.cli.output_projection",
        "agent.interfaces.cli.output_viewer",
        "agent.routing.persona.current",
        "agent.evaluation.experiment",
        "agent.evaluation.receipt",
        "agent.evaluation.comparison",
        "agent.evaluation.feedback",
        "agent.outputs.service",
    )
    for module in modules:
        assert importlib.import_module(module) is not None


def test_retired_facades_are_not_importable() -> None:
    for module in (
        "agent.llm.router",
        "agent.interfaces.cli.manifest",
        "agent.interfaces.cli.query_plane",
        "agent.interfaces.cli.query_find",
        "agent.interfaces.cli.query_git",
    ):
        try:
            importlib.import_module(module)
        except ModuleNotFoundError:
            continue
        raise AssertionError(f"retired module remains importable: {module}")

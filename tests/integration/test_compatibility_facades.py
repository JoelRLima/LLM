import importlib


def test_root_compatibility_modules_alias_the_canonical_implementations() -> None:
    aliases = {
        "cli_chat": "agent.interfaces.cli.chat",
        "cli_streaming": "agent.interfaces.cli.streaming",
        "command_handlers": "agent.interfaces.cli.command_handlers",
        "command_ui": "agent.interfaces.cli.ui",
        "commands": "agent.interfaces.cli.commands",
        "config": "agent.runtime.config",
        "config_validation": "agent.runtime.config_validation",
        "logger": "agent.runtime.logging",
        "paths": "agent.runtime.paths",
        "session": "agent.llm.session",
    }

    for legacy, canonical in aliases.items():
        assert importlib.import_module(legacy) is importlib.import_module(canonical)


def test_script_facades_export_the_canonical_entry_points() -> None:
    from agent.interfaces.cli.app import main as canonical_cli
    from benchmark import main as legacy_benchmark
    from cli import main as legacy_cli
    from scripts.benchmark import main as canonical_benchmark

    assert legacy_cli is canonical_cli
    assert legacy_benchmark is canonical_benchmark

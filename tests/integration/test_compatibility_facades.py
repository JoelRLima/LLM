from pathlib import Path


def test_root_compatibility_modules_are_retired() -> None:
    root = Path(__file__).resolve().parents[2]
    retired = (
        "cli.py",
        "cli_chat.py",
        "cli_streaming.py",
        "commands.py",
        "command_handlers.py",
        "command_ui.py",
        "config.py",
        "config_validation.py",
        "logger.py",
        "paths.py",
        "session.py",
        "benchmark.py",
    )

    assert all(not (root / name).exists() for name in retired)


def test_script_entry_points_are_explicitly_canonical() -> None:
    from agent.interfaces.cli.app import main as canonical_cli
    from scripts.benchmark import main as canonical_benchmark

    assert callable(canonical_cli)
    assert callable(canonical_benchmark)

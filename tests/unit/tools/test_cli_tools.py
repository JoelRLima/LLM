from pathlib import Path

from llm_agent.extensions.extension_registry import ExtensionRegistry
from llm_agent.interfaces.cli.app import build_parser


def test_cli_tools_commands_parse(tmp_path: Path) -> None:
    parser = build_parser()
    args = parser.parse_args(["tools", "list", "--state", str(tmp_path / "extensions.json")])
    assert args.command == "tools"
    assert args.tools_command == "list"
    assert args.state == str(tmp_path / "extensions.json")


def test_extension_registry_can_be_loaded_from_cli_state(tmp_path: Path) -> None:
    state_path = tmp_path / "extensions.json"
    registry = ExtensionRegistry(state_path)
    registry.add(id="demo.extension", manifest_path=tmp_path / "manifest.json")
    reloaded = ExtensionRegistry(state_path)
    assert reloaded.get("demo.extension") is not None

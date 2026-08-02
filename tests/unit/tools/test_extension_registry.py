import json
from pathlib import Path

from agent.tools.extension_registry import ExtensionRegistry


def test_extension_registry_persists_enabled_state(tmp_path: Path) -> None:
    registry = ExtensionRegistry(tmp_path / "extensions.json")

    registry.add(
        id="demo.extension",
        manifest_path=tmp_path / "manifest.json",
        enabled=True,
    )
    assert registry.get("demo.extension").enabled is True

    registry.set_enabled("demo.extension", False)
    assert registry.get("demo.extension").enabled is False

    data = json.loads((tmp_path / "extensions.json").read_text(encoding="utf-8"))
    assert data["demo.extension"]["enabled"] is False

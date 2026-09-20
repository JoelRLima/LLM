from __future__ import annotations

import json
from pathlib import Path

from agent.runtime.config_repository import packaged_config_defaults
from agent.runtime.paths import AppHomeOrigin, AppPaths
from agent.runtime.storage_bootstrap import StorageBootstrap


def test_migrated_home_is_source_preserving_and_converges_to_one_layout(tmp_path: Path) -> None:
    home = tmp_path / "home"
    legacy_config = home / "config" / "config.json"
    legacy_data = home / "data" / "workspaces" / "stable-id"
    legacy_state = home / "state" / "workspaces" / "stable-id"
    legacy_extensions = legacy_data / "extensions.json"
    legacy_memory = legacy_data / "agent_memory.json"
    legacy_history = legacy_data / "chat_history.json"
    legacy_checkpoint = legacy_state / "agent_checkpoint.json"
    legacy_report = legacy_state / "reports" / "old-report.json"
    for path in (legacy_config, legacy_extensions, legacy_memory, legacy_history, legacy_checkpoint, legacy_report):
        path.parent.mkdir(parents=True, exist_ok=True)
    legacy_config.write_text(json.dumps(packaged_config_defaults()), encoding="utf-8")
    legacy_extensions.write_text("{}", encoding="utf-8")
    legacy_memory.write_text('{"notes": {"migrated": true}}', encoding="utf-8")
    legacy_history.write_text("{}", encoding="utf-8")
    legacy_checkpoint.write_text("{}", encoding="utf-8")
    legacy_report.write_text('{"report": "migrated"}', encoding="utf-8")
    source_bytes = {
        path: path.read_bytes()
        for path in (legacy_config, legacy_extensions, legacy_memory, legacy_history, legacy_checkpoint, legacy_report)
    }

    paths = AppPaths.discover(app_home=home, env={})
    assert paths.home_origin is AppHomeOrigin.ARGUMENT
    first = StorageBootstrap().prepare(paths)
    assert first.migrated is True
    assert paths.storage_layout_file.exists()
    assert paths.w18_to_w19_migration_receipt_file.exists()

    canonical = paths.for_workspace("stable-id")
    assert canonical.memory_file.read_bytes() == source_bytes[legacy_memory]
    assert canonical.chat_history_file.read_bytes() == source_bytes[legacy_history]
    assert canonical.checkpoint_file.read_bytes() == source_bytes[legacy_checkpoint]
    assert canonical.extensions_file.read_bytes() == source_bytes[legacy_extensions]
    assert (canonical.reports_dir / "old-report.json").read_bytes() == source_bytes[legacy_report]
    assert not canonical.feedback_file.exists()
    assert not tuple(canonical.output_artifacts_dir.glob("*.json"))

    marker = paths.storage_layout_file.read_bytes()
    second = StorageBootstrap().prepare(paths)
    assert second.migrated is False
    assert paths.storage_layout_file.read_bytes() == marker
    for path, content in source_bytes.items():
        assert path.read_bytes() == content

import os
from pathlib import Path

import pytest

from agent.runtime.paths import AppPaths
from agent.runtime.workspace_context import WorkspaceContext


def test_explicit_home_is_resolved_without_creating_directories(tmp_path: Path) -> None:
    home = tmp_path / "home"

    paths = AppPaths.discover(home, env={})

    assert paths.config_file == (home / "config" / "config.json").resolve()
    assert paths.log_file == (home / "logs" / "agent.log").resolve()
    assert not home.exists()


def test_workspace_paths_are_isolated_by_stable_id(tmp_path: Path) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    first = WorkspaceContext.create(first_root)
    second = WorkspaceContext.create(second_root)
    app_paths = AppPaths.discover(tmp_path / "home", env={})

    first_paths = app_paths.for_workspace(first.workspace_id)
    second_paths = app_paths.for_workspace(second.workspace_id)

    assert first_paths.state_dir != second_paths.state_dir
    assert first_paths.memory_file != second_paths.memory_file
    assert not app_paths.state_dir.exists()


def test_workspace_resolution_blocks_escape(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    workspace = WorkspaceContext.create(root)

    assert workspace.resolve("nested/file.txt") == (root / "nested" / "file.txt").resolve()
    with pytest.raises(PermissionError, match="fora do workspace"):
        workspace.resolve("../outside.txt")


def test_workspace_resolution_expands_user_shorthand_before_confinement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    workspace = WorkspaceContext.create(root)
    expanded = tmp_path / "home" / "outside.txt"
    original_expanduser = Path.expanduser

    def expanduser(path: Path) -> Path:
        if str(path) == "~/outside.txt":
            return expanded
        return original_expanduser(path)

    monkeypatch.setattr(Path, "expanduser", expanduser)

    with pytest.raises(PermissionError, match="fora do workspace"):
        workspace.resolve("~/outside.txt")


def test_code_path_compatibility_facade_projects_runtime_owner() -> None:
    from agent.code import path_safety as legacy
    from agent.runtime import path_safety as canonical

    assert legacy.resolve_workspace_path is canonical.resolve_workspace_path
    assert legacy.workspace_relative_path is canonical.workspace_relative_path
    assert legacy.workspace_command_argument is canonical.workspace_command_argument


@pytest.mark.skipif(os.name == "nt", reason="Windows possui paths case-insensitive")
def test_case_sensitive_workspaces_have_distinct_ids(tmp_path: Path) -> None:
    upper = tmp_path / "A" / "work"
    lower = tmp_path / "a" / "work"
    upper.mkdir(parents=True)
    lower.mkdir(parents=True)

    assert WorkspaceContext.create(upper).workspace_id != WorkspaceContext.create(lower).workspace_id


def test_legacy_runtime_override_is_absolute(tmp_path: Path) -> None:
    paths = AppPaths.discover(env={"AGENT_RUNTIME_DIR": str(tmp_path / "runtime")})

    assert paths.state_dir.is_absolute()
    assert paths.state_dir == (tmp_path / "runtime").resolve()


def test_global_extension_catalog_paths_are_adjacent_to_legacy_registry(tmp_path: Path) -> None:
    paths = AppPaths.discover(tmp_path / "home", env={})

    assert paths.extensions_registry_file == paths.extensions_dir / "registry.json"
    assert paths.extensions_catalog_file == paths.extensions_dir / "catalog.json"
    assert paths.extensions_catalog_lock_file == paths.extensions_dir / "catalog.json.lock"
    assert not paths.extensions_dir.exists()


def test_workspace_extension_configuration_and_lock_are_isolated(tmp_path: Path) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    app_paths = AppPaths.discover(tmp_path / "home", env={})
    first = app_paths.for_workspace(WorkspaceContext.create(first_root).workspace_id)
    second = app_paths.for_workspace(WorkspaceContext.create(second_root).workspace_id)

    assert first.workspace_extensions_file == first.extensions_file
    assert first.workspace_extensions_lock_file.parent == first.workspace_extensions_file.parent
    assert first.workspace_extensions_lock_file.name == "extensions.json.lock"
    assert first.workspace_extensions_file != second.workspace_extensions_file
    assert first.workspace_extensions_lock_file != second.workspace_extensions_lock_file
    assert not first.workspace_extensions_file.exists()

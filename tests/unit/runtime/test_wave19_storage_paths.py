from __future__ import annotations

from pathlib import Path

import pytest

from agent.runtime import paths as paths_module
from agent.runtime.paths import AppHomeOrigin, AppPaths


def test_explicit_home_is_one_side_effect_free_root(tmp_path: Path) -> None:
    home = tmp_path / "home"
    paths = AppPaths.discover(app_home=home, env={})

    assert paths.home_origin is AppHomeOrigin.ARGUMENT
    assert paths.home_dir == home.resolve()
    assert paths.config_dir == paths.home_dir / "config"
    assert paths.global_dir == paths.home_dir / "global"
    assert paths.workspaces_dir == paths.home_dir / "workspaces"
    assert paths.cache_dir == paths.home_dir / "cache"
    assert paths.log_dir == paths.home_dir / "logs"
    assert not home.exists()


def test_discovery_precedence_is_exact(tmp_path: Path) -> None:
    argument = AppPaths.discover(
        app_home=tmp_path / "argument",
        env={"LLM_AGENT_HOME": str(tmp_path / "llm"), "AGENT_RUNTIME_DIR": str(tmp_path / "runtime")},
    )
    assert argument.home_origin is AppHomeOrigin.ARGUMENT

    llm_home = AppPaths.discover(
        env={"LLM_AGENT_HOME": str(tmp_path / "llm"), "AGENT_RUNTIME_DIR": str(tmp_path / "runtime")}
    )
    assert llm_home.home_origin is AppHomeOrigin.LLM_AGENT_HOME
    assert llm_home.home_dir == (tmp_path / "llm").resolve()

    runtime = AppPaths.discover(env={"AGENT_RUNTIME_DIR": str(tmp_path / "runtime")})
    assert runtime.home_origin is AppHomeOrigin.AGENT_RUNTIME_DIR
    assert runtime.home_dir == (tmp_path / "runtime").resolve()


def test_platform_default_uses_dedicated_home_child(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    if paths_module.os.name == "nt":
        local = tmp_path / "local"
        paths = AppPaths.discover(
            env={"LOCALAPPDATA": str(local), "APPDATA": str(tmp_path / "roaming")}
        )
        assert paths.home_origin is AppHomeOrigin.WINDOWS_DEFAULT
        assert paths.home_dir == local / "local-llm-agent" / "home"
    else:
        data = tmp_path / "data"
        paths = AppPaths.discover(env={"XDG_DATA_HOME": str(data)})
        assert paths.home_origin is AppHomeOrigin.XDG_DEFAULT
        assert paths.home_dir == data / "local-llm-agent" / "home"


def test_workspace_paths_are_grouped_and_additions_are_semantic(tmp_path: Path) -> None:
    paths = AppPaths.discover(app_home=tmp_path / "home", env={})
    workspace = paths.for_workspace("alpha-01")

    assert workspace.data_dir.parent == workspace.state_dir.parent == workspace.cache_dir.parent
    assert workspace.data_dir.parent == paths.workspaces_dir / "alpha-01"
    assert workspace.output_artifacts_dir == workspace.artifacts_dir / "outputs"
    assert workspace.feedback_file == workspace.data_dir / "human_feedback.json"
    assert workspace.feedback_lock_file == workspace.data_dir / "human_feedback.json.lock"


@pytest.mark.parametrize("workspace_id", ["", "/tmp", "..", "a/b", "a\\b", "-bad"])
def test_workspace_id_validation_fails_closed(tmp_path: Path, workspace_id: str) -> None:
    paths = AppPaths.discover(app_home=tmp_path / "home", env={})
    with pytest.raises(ValueError):
        paths.for_workspace(workspace_id)


def test_injected_paths_do_not_probe_platform_sources(tmp_path: Path) -> None:
    paths = AppPaths(
        home_dir=tmp_path / "injected",
        config_dir=tmp_path / "injected" / "config",
        global_dir=tmp_path / "injected" / "global",
        workspaces_dir=tmp_path / "injected" / "workspaces",
        cache_dir=tmp_path / "injected" / "cache",
        log_dir=tmp_path / "injected" / "logs",
    )
    assert paths.home_origin is AppHomeOrigin.INJECTED

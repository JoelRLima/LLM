from __future__ import annotations

import json

import pytest

from llm_agent.agent.runtime.config_repository import ConfigRepository
from llm_agent.application.task_execution import TaskExecutionStart
from llm_agent.application.workspace_recents import remember_recent_workspace
from llm_agent.interactive.session import InteractiveSession
from llm_agent.interfaces.workbench.webview import WorkbenchBridge
from llm_agent.workspace.paths import AppPaths


@pytest.fixture
def navigation_session(tmp_path):
    workspace = tmp_path / "Projeto com espaços"
    workspace.mkdir()
    paths = AppPaths.discover(tmp_path / "home", env={})
    ConfigRepository(paths).initialize()
    session = InteractiveSession(TaskExecutionStart(workspace, paths, configure_logging=False))
    yield session
    session.close().result(timeout=10)


def test_navigation_projects_only_safe_profile_labels(navigation_session, monkeypatch, tmp_path):
    from llm_agent.interactive import session_navigation

    session = navigation_session
    monkeypatch.setattr(session_navigation, "_load_config", lambda _: {
        "model_profiles": {"Local": {"provider": "openai_compatible", "model": "small-model",
                                     "api_key": "private-secret", "base_url": "https://private-endpoint"}},
        "other_secret": "private-global",
    })
    recent = tmp_path / "Outro projeto"
    recent.mkdir()
    remember_recent_workspace(session.start.app_paths, recent)
    options = WorkbenchBridge(session=session).navigation_options()
    assert options == {"accepted": True, "workspace_id": session.workspace.workspace_id,
                       "profiles": [{"name": "Local", "provider": "openai_compatible", "model": "small-model"}],
                       "recent_workspaces": [str(recent.resolve())]}
    assert "private" not in json.dumps(options)
    assert session._runtime is None
    assert session.workspace.root != recent


def test_navigation_read_is_rejected_while_session_busy(navigation_session, monkeypatch):
    session = navigation_session
    monkeypatch.setattr(session.controller, "is_busy", lambda: True)
    assert WorkbenchBridge(session=session).navigation_options()["accepted"] is False
    assert session._runtime is None


def test_navigation_reports_read_failure_without_changing_session(navigation_session, monkeypatch):
    from llm_agent.interactive import session_navigation

    def fail(_):
        raise ValueError("config unavailable")

    monkeypatch.setattr(session_navigation, "_load_config", fail)
    result = WorkbenchBridge(session=navigation_session).navigation_options()
    assert result["accepted"] is True
    assert result["profiles"] == []
    assert result["configuration_error"] == "config unavailable"
    assert "recent_workspaces" in result
    assert navigation_session._runtime is None


def test_navigation_bridge_rejects_closed_or_legacy_session(navigation_session):
    bridge = WorkbenchBridge(session=navigation_session)
    bridge._closed = True
    assert bridge.navigation_options()["accepted"] is False
    legacy = WorkbenchBridge(session=object())
    assert legacy.navigation_options()["accepted"] is False


def test_navigation_options_are_detached_and_bounded(navigation_session, monkeypatch):
    from llm_agent.interactive import session_navigation

    config = {"model_profiles": {f"profile-{n}": {"provider": "fixture", "model": "small"} for n in range(110)}}
    monkeypatch.setattr(session_navigation, "_load_config", lambda _: config)
    first = navigation_session.navigation_options()
    assert len(first["profiles"]) == 100
    first["profiles"][0]["name"] = "changed"
    assert navigation_session.navigation_options()["profiles"][0]["name"] == "profile-0"


def test_real_config_choices_use_existing_navigation_without_model_runtime(navigation_session, tmp_path):
    session = navigation_session
    choices = WorkbenchBridge(session=session).navigation_options()
    assert choices["accepted"] is True
    profile = choices["profiles"][0]["name"]
    outcome, _ = session.submit(f"/model select {profile}").result(timeout=10)
    assert outcome["status"] == "succeeded"
    assert session.start.profile == profile
    target = tmp_path / "Próximo projeto com espaços"
    target.mkdir()
    outcome, _ = session.submit(f"/workspace switch {target}").result(timeout=10)
    assert outcome["session_changed"] is True
    refreshed = WorkbenchBridge(session=session).navigation_options()
    assert refreshed["workspace_id"] != choices["workspace_id"]
    assert str(target.resolve()) in refreshed["recent_workspaces"]
    assert session._runtime is None

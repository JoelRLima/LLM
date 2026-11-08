from __future__ import annotations

import json
from pathlib import Path

import pytest

from llm_agent.agent.application import AgentApplication
from llm_agent.agent.approval import AutoApprove
from llm_agent.agent.capabilities import Capability
from llm_agent.agent.planning.planning_context import build_planning_context
from llm_agent.agent.runtime.config_repository import ConfigRepository
from llm_agent.agent.tools.contracts import ToolStatus
from llm_agent.application.plugin_administration import PluginAdministration
from llm_agent.application.task_execution import TaskExecutionStart
from llm_agent.interactive.admission import build_natural_envelope
from llm_agent.interactive.session import InteractiveSession
from llm_agent.interfaces.workbench.webview import WorkbenchBridge
from llm_agent.workspace.paths import AppPaths
from tests.support.offline_scenarios import OfflineChatGateway


@pytest.fixture
def plugins(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    paths = AppPaths.discover(tmp_path / "home", env={})
    ConfigRepository(paths).initialize()
    session = InteractiveSession(TaskExecutionStart(workspace, paths, configure_logging=False))
    bridge = WorkbenchBridge(session=session)
    manifests = {}
    source = Path(__file__).resolve().parents[3] / "examples/extensions/demo_extension"
    for name in ("alpha", "beta"):
        directory = tmp_path / name
        directory.mkdir()
        script = (source / "demo_extension.py").read_text(encoding="utf-8").replace("demo_tool", f"{name}_tool")
        (directory / "demo_extension.py").write_text(script, encoding="utf-8")
        manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
        manifest["id"] = f"{name}.extension"
        manifest["tools"][0]["name"] = f"{name}_tool"
        path = directory / "manifest.json"
        path.write_text(json.dumps(manifest), encoding="utf-8")
        manifests[name] = path
    yield session, bridge, manifests
    session.close().result(timeout=10)


def action(plugins, operation, name="alpha", value=""):
    session, bridge, _ = plugins
    state = bridge.plugin_state()
    row = next((item for item in state["items"] if item["extension_id"] == f"{name}.extension"), {})
    result = bridge.plugin_action(
        session.workspace.workspace_id, operation, f"{name}.extension", value, row.get("manifest_sha256") or ""
    )
    assert result["accepted"], result
    return result


def prepare(plugins, name="alpha"):
    action(plugins, "register", name, str(plugins[2][name]))
    action(plugins, "enable", name)
    action(plugins, "grant", name, "read")
    return action(plugins, "grant", name, "process")


def test_manager_separates_registration_grants_and_session_authority_without_spawn(plugins, monkeypatch):
    import subprocess

    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("administration must not spawn"))
    state = action(plugins, "register", value=str(plugins[2]["alpha"]))
    assert state["items"][0]["activation_status"] == "not_enabled"
    assert plugins[0].start.startup_capabilities is None
    action(plugins, "enable")
    action(plugins, "grant", value="read")
    session, bridge, _ = plugins
    row = bridge.plugin_state()["items"][0]
    rejected = bridge.plugin_action(
        session.workspace.workspace_id, "authorize", row["extension_id"], "", row["manifest_sha256"]
    )
    assert rejected["accepted"] is False
    action(plugins, "grant", value="process")
    state = action(plugins, "authorize")
    assert state["items"][0]["session_authorized"] is True
    assert session.start.startup_extension_ids == ("alpha.extension",)
    assert session._runtime is None


def test_plugin_execution_review_is_bound_to_manifest_and_discloses_os_access(plugins):
    state = prepare(plugins)
    row = state["items"][0]
    execution = row["execution"]
    assert execution["review_status"] == "matched"
    assert execution["sandboxed"] is False
    assert execution["fingerprint_scope"] == "manifest_only"
    assert execution["identity"] == "current_os_user"
    assert execution["cwd"] == str(plugins[0].workspace.root)
    assert json.loads(execution["command"])
    manifest = plugins[2]["alpha"]
    manifest.write_text(manifest.read_text() + "\n")
    changed = plugins[1].plugin_state()["items"][0]
    assert changed["execution"]["command"] is None
    assert changed["execution"]["review_status"] == "changed"


def test_selected_id_is_enforced_in_planning_and_execution_even_with_equal_capabilities(plugins):
    prepare(plugins, "alpha")
    prepare(plugins, "beta")
    action(plugins, "authorize", "alpha")
    session = plugins[0]
    with AgentApplication.create(
        workspace=session.workspace.root,
        paths=session.start.app_paths,
        gateway=OfflineChatGateway("unused"),
        approval_policy=AutoApprove(),
        configure_logging=False,
        task_authority_capabilities=session.start.startup_capabilities,
        task_authority_extension_ids=session.start.startup_extension_ids,
        task_authority_extension_fingerprints=dict(session.start.startup_extension_fingerprints),
    ) as app:
        context = build_planning_context(
            app.tool_registry,
            app.application_authority,
            app.task_authority,
            persona_restrictions=frozenset(cap.value for cap in Capability),
        )
        assert "alpha_tool" in context.eligible_names
        assert "beta_tool" not in context.eligible_names
        denied = app.tool_invocation_gateway.run("beta_tool", {"text": "blocked"})
        assert denied.status is ToolStatus.PERMISSION_DENIED
        assert denied.executed is False
        result = app.tool_invocation_gateway.run("alpha_tool", {"text": "hello"})
        assert result.status is ToolStatus.SUCCEEDED
        assert result.data == {"echo": "hello"}


def test_session_supports_multiple_selected_plugins_and_removal(plugins):
    prepare(plugins, "alpha")
    prepare(plugins, "beta")
    action(plugins, "authorize", "alpha")
    action(plugins, "authorize", "beta")
    assert plugins[0].start.startup_extension_ids == ("alpha.extension", "beta.extension")
    state = action(plugins, "deauthorize", "alpha")
    assert state["authorized_ids"] == ["beta.extension"]
    action(plugins, "clear_authority")
    assert plugins[0].start.startup_capabilities is None


def test_interactive_runtime_uses_reviewed_scope_and_preserves_read_only_mode(plugins, monkeypatch):
    prepare(plugins)
    action(plugins, "authorize")
    original = AgentApplication.create

    def create(**kwargs):
        return original(**kwargs, gateway=OfflineChatGateway("unused"))

    monkeypatch.setattr(AgentApplication, "create", create)
    session = plugins[0]
    runtime = session._ensure_runtime()
    assert runtime._owner.task_authority.allowed_extension_ids == frozenset({"alpha.extension"})
    assert session.execution_mode().replace(" ", "_") == "READ_ONLY"
    blocked = runtime._owner.tool_invocation_gateway.run("alpha_tool", {"text": "not executed"})
    assert blocked.status is ToolStatus.PERMISSION_DENIED
    assert blocked.executed is False


def test_admin_change_revokes_session_authority_and_preserves_workspace_grants(plugins):
    prepare(plugins)
    action(plugins, "authorize")
    action(plugins, "disable")
    state = plugins[1].plugin_state()
    assert state["authorized_ids"] == []
    assert set(state["items"][0]["grants"]) == {"read", "process"}


def test_workspace_switch_clears_plugin_authority(plugins, tmp_path):
    prepare(plugins)
    action(plugins, "authorize")
    target = tmp_path / "another-workspace"
    target.mkdir()
    observation, _ = plugins[0].submit(f"/workspace switch {target}").result(timeout=10)
    assert observation["session_changed"] is True
    assert plugins[0].start.startup_extension_ids is None
    assert plugins[1].plugin_state()["authorized_ids"] == []


def test_reviewed_manifest_is_pinned_across_runtime_bootstrap(plugins):
    prepare(plugins)
    action(plugins, "authorize")
    session, _, manifests = plugins
    path = manifests["alpha"]
    service = PluginAdministration(session.start.app_paths, session.workspace)
    old = service.catalog.load().entries[0].manifest_sha256
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    service.catalog.replace("alpha.extension", path, expected_fingerprint=old)
    with pytest.raises(ValueError, match="Plugin changed"):
        AgentApplication.create(
            workspace=session.workspace.root,
            paths=session.start.app_paths,
            gateway=OfflineChatGateway("unused"),
            configure_logging=False,
            task_authority_capabilities=session.start.startup_capabilities,
            task_authority_extension_ids=session.start.startup_extension_ids,
            task_authority_extension_fingerprints=dict(session.start.startup_extension_fingerprints),
        )


def test_manifest_update_requires_new_review_and_removal_preserves_plugin_files(plugins):
    prepare(plugins)
    action(plugins, "authorize")
    session, bridge, manifests = plugins
    path = manifests["alpha"]
    old = bridge.plugin_state()["items"][0]["manifest_sha256"]
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    row = bridge.plugin_state()["items"][0]
    assert row["manifest_status"] == "changed"
    failed = bridge.plugin_action(session.workspace.workspace_id, "replace", row["extension_id"], "0" * 64, old)
    assert failed["accepted"] is False
    assert bridge.plugin_state()["items"][0]["manifest_sha256"] == old
    state = action(plugins, "replace", value=row["observed_sha256"])
    assert state["items"][0]["manifest_sha256"] == row["observed_sha256"]
    assert state["authorized_ids"] == []
    state = action(plugins, "remove")
    assert state["items"] == []
    assert path.is_file()
    assert (path.parent / "demo_extension.py").is_file()


def test_orphan_configuration_can_be_forgotten_without_granting_authority(plugins):
    prepare(plugins)
    session, bridge, _ = plugins
    PluginAdministration(session.start.app_paths, session.workspace).catalog.remove("alpha.extension")
    state = bridge.plugin_state()
    assert state["items"][0]["manifest_sha256"] is None
    assert action(plugins, "forget")["items"] == []
    assert session.start.startup_capabilities is None


@pytest.mark.parametrize("condition", ["workspace", "fingerprint", "busy", "events", "pending", "closed"])
def test_rejects_stale_or_unavailable_management_requests(plugins, monkeypatch, condition):
    prepare(plugins)
    session, bridge, _ = plugins
    row = bridge.plugin_state()["items"][0]
    workspace_id = session.workspace.workspace_id
    fingerprint = row["manifest_sha256"]
    if condition == "workspace":
        workspace_id = "another-workspace"
    if condition == "fingerprint":
        fingerprint = "0" * 64
    if condition == "busy":
        monkeypatch.setattr(session.controller, "is_busy", lambda: True)
    if condition == "events":
        bridge._events.append({"type": "settled"})
    if condition == "pending":
        session.controller.pending.add(build_natural_envelope("pending request"))
    if condition == "closed":
        bridge._closed = True
    result = bridge.plugin_action(workspace_id, "authorize", row["extension_id"], "", fingerprint)
    assert result["accepted"] is False
    assert session.start.startup_capabilities is None

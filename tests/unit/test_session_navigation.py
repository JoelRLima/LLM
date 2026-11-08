from pathlib import Path
from types import SimpleNamespace

import pytest

from llm_agent.application.context import AppPaths, WorkspaceContext
from llm_agent.application.inspection import InspectionListRequest, InspectionReadContext, list_inspection_runs
from llm_agent.interactive import session_navigation


def _match(action_id, payload=""):
    return SimpleNamespace(action_id=action_id, raw_payload=payload)


def test_supported_actions_are_finite():
    assert session_navigation.ACTIONS == {
        "model.show", "model.select", "workspace.show", "workspace.switch", "inspection.open"
    }


def test_workspace_show_returns_primitive_observation():
    workspace = SimpleNamespace(
        workspace_id="ws-1", root=Path.cwd(),
    )
    session = SimpleNamespace(workspace=workspace)

    observation, reference = session_navigation.execute(session, _match("workspace.show"))

    assert reference is None
    assert observation["workspace_id"] == "ws-1"
    assert observation["workspace_root"] == str(Path.cwd())
    assert observation["workspace_name"] == Path.cwd().name


def test_inspection_list_uses_application_api_and_returns_answer_string():
    workspace = WorkspaceContext.create(Path.cwd())
    app_paths = AppPaths.discover(Path.cwd() / ".tmp" / "session-navigation-test-home")
    expected = list_inspection_runs(
        InspectionReadContext(app_paths.for_workspace(workspace.workspace_id)), InspectionListRequest()
    )
    session = SimpleNamespace(workspace=workspace, start=SimpleNamespace(app_paths=app_paths))

    observation, reference = session_navigation.execute(session, _match("inspection.open", "list"))

    assert reference is None
    assert observation["runs"] == [dict(run) for run in expected]
    assert isinstance(observation["answer"], str)


def test_unknown_action_is_rejected():
    with pytest.raises(ValueError, match="unsupported session navigation action"):
        session_navigation.execute(SimpleNamespace(), _match("run.start"))


def test_workspace_switch_requires_path():
    with pytest.raises(ValueError, match="requires a workspace path"):
        session_navigation.execute(SimpleNamespace(), _match("workspace.switch"))

from __future__ import annotations

from pathlib import Path

import pytest

from llm_agent.agent.task_definition.errors import TaskDefinitionError
from llm_agent.agent.task_definition.repository import TaskDefinitionRepository
from llm_agent.agent.task_definition.resolver import TaskContextResolver
from llm_agent.application.context import AppPaths, WorkspaceContext, WorkspacePaths
from llm_agent.application.task_context import (
    TaskContextReadError,
    TaskContextRequest,
    TaskContextResult,
    read_task_context,
)
from tests.support.task_definition import make_contract, make_phase, make_spec


def _workspace_paths(home: Path, workspace_root: Path) -> tuple[AppPaths, WorkspacePaths]:
    context = WorkspaceContext.create(workspace_root)
    app_paths = AppPaths.discover(home, env={})
    paths = app_paths.for_workspace(context.workspace_id)
    paths.ensure_directories()
    return app_paths, paths


@pytest.mark.parametrize("phase_id", [None, "phase-2"])
def test_task_context_application_matches_agent_materialization(
    tmp_path: Path,
    phase_id: str | None,
) -> None:
    home = tmp_path / "home"
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    app_paths, workspace_paths = _workspace_paths(home, workspace_root)
    repository = TaskDefinitionRepository(workspace_paths)
    contract = make_contract("task-context", "Read task authority")
    repository.save_contract(contract)
    repository.save_spec(
        make_spec(
            contract,
            phases=(make_phase("phase-1"), make_phase("phase-2", depends_on=("phase-1",))),
        )
    )
    legacy_materialization = TaskContextResolver(repository).resolve(
        "task-context",
        phase_id=phase_id,
    )

    request = TaskContextRequest(
        app_paths=app_paths,
        workspace=workspace_root,
        task_id="task-context",
        phase_id=phase_id,
    )
    result = read_task_context(request)

    assert isinstance(result, TaskContextResult)
    assert result.to_dict() == legacy_materialization.to_dict()
    assert result.to_dict()["context"] == result.trusted_text
    assert isinstance(result.to_dict()["authority"], dict)
    assert read_task_context(request).to_dict() == result.to_dict()


@pytest.mark.parametrize(
    ("task_id", "phase_id", "incomplete"),
    [
        ("missing-task", None, False),
        ("task-context", "missing-phase", False),
        ("incomplete-task", None, True),
    ],
)
def test_task_context_application_translates_definition_errors_only(
    tmp_path: Path,
    task_id: str,
    phase_id: str | None,
    incomplete: bool,
) -> None:
    home = tmp_path / "home"
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    app_paths, workspace_paths = _workspace_paths(home, workspace_root)
    repository = TaskDefinitionRepository(workspace_paths)
    contract = make_contract("task-context", "Read task authority")
    repository.save_contract(contract)
    if not incomplete:
        repository.save_spec(make_spec(contract))
    else:
        repository.save_contract(make_contract("incomplete-task", "Incomplete authority"))

    with pytest.raises(TaskContextReadError) as raised:
        read_task_context(
            TaskContextRequest(
                app_paths=app_paths,
                workspace=workspace_root,
                task_id=task_id,
                phase_id=phase_id,
            )
        )

    assert isinstance(raised.value.__cause__, TaskDefinitionError)


def test_task_context_application_does_not_translate_workspace_errors_or_create_directories(
    tmp_path: Path,
) -> None:
    home = tmp_path / "not-created-home"
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    request = TaskContextRequest(
        app_paths=AppPaths.discover(home, env={}),
        workspace=workspace_root,
        task_id="missing-task",
    )

    with pytest.raises(TaskContextReadError):
        read_task_context(request)
    assert not home.exists()

    missing_workspace_request = TaskContextRequest(
        app_paths=request.app_paths,
        workspace=tmp_path / "missing-workspace",
        task_id="task-context",
    )
    with pytest.raises(FileNotFoundError) as raised:
        read_task_context(missing_workspace_request)
    assert not isinstance(raised.value, TaskContextReadError)
    assert not home.exists()

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.interfaces.cli import app as cli
from agent.interfaces.cli.parser import build_parser
from agent.runtime.paths import AppPaths
from agent.runtime.workspace_context import WorkspaceContext
from agent.task_definition.repository import TaskDefinitionRepository
from tests.support.task_definition import make_contract, make_spec


def _persist_cli_definition(home: Path, workspace_root: Path) -> None:
    workspace = WorkspaceContext.create(workspace_root)
    paths = AppPaths.discover(home, env={}).for_workspace(workspace.workspace_id)
    paths.ensure_directories()
    repository = TaskDefinitionRepository(paths)
    contract = make_contract("cli-task", "CLI objective")
    repository.save_contract(contract)
    repository.save_spec(make_spec(contract))


def test_parser_exposes_read_only_task_context_command() -> None:
    args = build_parser().parse_args(
        [
            "task",
            "context",
            "--workspace",
            "workspace",
            "--task-id",
            "task-1",
            "--phase",
            "phase-1",
            "--json",
        ]
    )

    assert args.command == "task"
    assert args.task_command == "context"
    assert args.task_id == "task-1"
    assert args.phase_id == "phase-1"
    assert args.json_output is True


def test_task_context_cli_is_model_free_read_only_and_deterministic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    home = tmp_path / "home"
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    _persist_cli_definition(home, workspace_root)
    before = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*"))

    monkeypatch.setattr(
        cli,
        "_create_application",
        lambda *_args, **_kwargs: pytest.fail("task context must not create an application"),
    )
    arguments = [
        "task",
        "context",
        "--home",
        str(home),
        "--workspace",
        str(workspace_root),
        "--task-id",
        "cli-task",
        "--phase",
        "phase-1",
        "--json",
    ]
    assert cli.main(arguments) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["task_id"] == "cli-task"
    assert first["phase_id"] == "phase-1"
    assert first["context"].startswith("--- TASK DEFINITION AUTHORITY")

    assert cli.main(arguments) == 0
    second = json.loads(capsys.readouterr().out)
    assert second == first
    after = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*"))
    assert after == before


@pytest.mark.parametrize(
    ("task_id", "phase"),
    [("missing-task", None), ("cli-task", "missing-phase")],
)
def test_task_context_cli_fails_without_fallback_or_mutation(
    tmp_path: Path,
    task_id: str,
    phase: str | None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    home = tmp_path / "home"
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    _persist_cli_definition(home, workspace_root)
    before = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*"))
    arguments = [
        "task",
        "context",
        "--home",
        str(home),
        "--workspace",
        str(workspace_root),
        "--task-id",
        task_id,
        "--json",
    ]
    if phase is not None:
        arguments.extend(["--phase", phase])

    assert cli.main(arguments) == 2
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "failed"
    after = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*"))
    assert after == before

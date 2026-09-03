from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.interfaces.cli import app as cli
from agent.interfaces.cli.parser import build_parser
from agent.runtime.paths import AppPaths
from agent.runtime.workspace_context import WorkspaceContext


def _checkpoint(paths: AppPaths, workspace: Path, *, definition_state: str = "complete") -> Path:
    workspace_paths = paths.for_workspace(WorkspaceContext.create(workspace).workspace_id)
    workspace_paths.ensure_directories()
    task_definition = {
        "task_id": "root-task",
        "contract_version": 1,
        "contract_digest": "0" * 64,
        "spec_version": 1,
        "spec_digest": "1" * 64,
        "definition_state": definition_state,
    }
    if definition_state == "contract_ready":
        task_definition.pop("spec_version")
        task_definition.pop("spec_digest")
    payload = {
        "schema_version": 2,
        "objective": "continuar a tarefa",
        "root_task_id": "root-task",
        "task_definition": task_definition,
        "plan": [],
        "plan_step": 0,
        "current_step_id": None,
        "step_records": [],
        "requested_effects": [],
        "executed_effects": [],
        "waived_effects": [],
        "prohibited_effects": [],
        "terminal_disposition": None,
        "hierarchical_lifecycle": {"status": "inactive"},
    }
    workspace_paths.checkpoint_file.write_text(json.dumps(payload), encoding="utf-8")
    return workspace_paths.checkpoint_file


def test_parser_exposes_explicit_continuity_commands() -> None:
    parser = build_parser()

    status = parser.parse_args(["task", "status", "--json"])
    resume = parser.parse_args(["task", "resume", "--yes", "--task-authority", "write"])

    assert status.task_command == "status"
    assert status.json_output is True
    assert resume.task_command == "resume"
    assert resume.assume_yes is True
    assert resume.task_authority_capabilities == ["write"]


def test_task_status_is_model_free_bounded_and_read_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    paths = AppPaths.discover(home, env={})
    checkpoint = _checkpoint(paths, workspace)
    before = checkpoint.read_bytes()
    monkeypatch.setattr(
        cli,
        "_create_application",
        lambda *_args, **_kwargs: pytest.fail("task status must not create an application"),
    )

    arguments = [
        "task",
        "status",
        "--home",
        str(home),
        "--workspace",
        str(workspace),
        "--json",
    ]
    assert cli.main(arguments) == 0

    document = json.loads(capsys.readouterr().out)
    assert document["status"] == "resumable"
    assert document["resumable"] is True
    assert document["root_task_id"] == "root-task"
    assert checkpoint.read_bytes() == before


def test_task_resume_absent_refuses_without_bootstrap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(
        cli,
        "_create_application",
        lambda *_args, **_kwargs: pytest.fail("unavailable resume must not create an application"),
    )

    assert cli.main(
        ["task", "resume", "--home", str(home), "--workspace", str(workspace), "--json"]
    ) == 2

    document = json.loads(capsys.readouterr().out)
    assert document["status"] == "failed"
    assert document["reason_code"] == "CHECKPOINT_ABSENT"
    assert document["continuity"]["status"] == "absent"


@pytest.mark.parametrize(
    ("missing_field", "reason_code"),
    [
        ("root_task_id", "CHECKPOINT_ROOT_TASK_ID_MISSING"),
        ("task_definition", "TASK_DEFINITION_BINDING_MISSING"),
    ],
)
def test_task_resume_unbound_checkpoint_refuses_without_bootstrap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    missing_field: str,
    reason_code: str,
) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    paths = AppPaths.discover(home, env={})
    checkpoint = _checkpoint(paths, workspace)
    payload = json.loads(checkpoint.read_text(encoding="utf-8"))
    payload.pop(missing_field)
    checkpoint.write_text(json.dumps(payload), encoding="utf-8")
    before = checkpoint.read_bytes()
    monkeypatch.setattr(
        cli,
        "_create_application",
        lambda *_args, **_kwargs: pytest.fail("unbound resume must not create an application"),
    )

    status_args = [
        "task",
        "status",
        "--home",
        str(home),
        "--workspace",
        str(workspace),
        "--json",
    ]
    assert cli.main(status_args) == 2
    status_document = json.loads(capsys.readouterr().out)
    assert status_document["status"] == "invalid"
    assert status_document["resumable"] is False
    assert status_document["reason_code"] == reason_code

    assert cli.main(
        ["task", "resume", "--home", str(home), "--workspace", str(workspace), "--json"]
    ) == 2
    resume_document = json.loads(capsys.readouterr().out)
    assert resume_document["status"] == "failed"
    assert resume_document["reason_code"] == reason_code
    assert checkpoint.read_bytes() == before


def test_task_resume_contract_ready_refuses_from_checkpoint_fact_without_bootstrap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    paths = AppPaths.discover(home, env={})
    checkpoint = _checkpoint(paths, workspace, definition_state="contract_ready")
    before = checkpoint.read_bytes()
    monkeypatch.setattr(
        cli,
        "_create_application",
        lambda *_args, **_kwargs: pytest.fail("incomplete resume must not create an application"),
    )

    status_args = [
        "task",
        "status",
        "--home",
        str(home),
        "--workspace",
        str(workspace),
        "--json",
    ]
    assert cli.main(status_args) == 0
    status_document = json.loads(capsys.readouterr().out)
    assert status_document["status"] == "unsupported"
    assert status_document["resumable"] is False
    assert status_document["reason_code"] == "TASK_DEFINITION_INCOMPLETE"
    assert checkpoint.read_bytes() == before

    assert cli.main(
        ["task", "resume", "--home", str(home), "--workspace", str(workspace), "--json"]
    ) == 2
    resume_document = json.loads(capsys.readouterr().out)
    assert resume_document["status"] == "failed"
    assert resume_document["reason_code"] == "TASK_DEFINITION_INCOMPLETE"
    assert resume_document["continuity"]["status"] == "unsupported"
    assert checkpoint.read_bytes() == before

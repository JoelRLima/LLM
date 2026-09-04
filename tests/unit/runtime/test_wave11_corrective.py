from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from agent.continuity.checkpoint_projection import classify_checkpoint_document
from agent.continuity.models import TaskContinuityStatus
from agent.interfaces.cli import app as cli
from agent.runtime.paths import AppPaths
from agent.runtime.task_directives import (
    DeliberationProfile,
    TaskDirective,
    TaskRunDirective,
)
from agent.runtime.workspace_context import WorkspaceContext


def _checkpoint(
    *,
    objective: str = "old W10 objective",
    task_run_directive: object = None,
    include_directive: bool = False,
    plan: list[dict[str, object]] | None = None,
    terminal_disposition: str | None = None,
) -> dict[str, object]:
    selected_plan = plan or []
    records = [
        {
            "step_id": step["_step_id"],
            "status": "pending",
            "attempts": 0,
            "last_error": "",
        }
        for step in selected_plan
    ]
    payload: dict[str, object] = {
        "schema_version": 2,
        "objective": objective,
        "root_task_id": "root-task",
        "task_definition": {
            "task_id": "root-task",
            "contract_version": 1,
            "contract_digest": "0" * 64,
            "spec_version": 1,
            "spec_digest": "1" * 64,
            "definition_state": "complete",
        },
        "plan": selected_plan,
        "plan_step": 0,
        "current_step_id": None,
        "step_records": records,
        "requested_effects": [],
        "executed_effects": [],
        "waived_effects": [],
        "prohibited_effects": [],
        "terminal_disposition": terminal_disposition,
        "hierarchical_lifecycle": {"status": "inactive"},
    }
    if include_directive:
        payload["task_run_directive"] = task_run_directive
    return payload


def _plan() -> list[dict[str, object]]:
    return [{"tool": "echo", "args": {}, "_step_id": "step-1"}]


def test_old_w10_checkpoint_without_w11_field_remains_resumable() -> None:
    snapshot = classify_checkpoint_document(_checkpoint())

    assert snapshot.status is TaskContinuityStatus.RESUMABLE
    assert snapshot.resumable is True


@pytest.mark.parametrize(
    "raw",
    [
        {
            "schema_version": 1,
            "directive": "read",
            "deliberation_profile": "normal",
            "subject": "old W10 objective",
            "unknown": True,
        },
        {
            "schema_version": 1,
            "directive": "read",
            "deliberation_profile": "unknown",
            "subject": "old W10 objective",
        },
        {
            "schema_version": 2,
            "directive": "read",
            "deliberation_profile": "normal",
            "subject": "old W10 objective",
        },
        {
            "schema_version": 1,
            "directive": "read",
            "deliberation_profile": "normal",
            "subject": "different objective",
        },
    ],
)
def test_present_invalid_w11_checkpoint_relation_is_not_resumable(
    raw: dict[str, object],
) -> None:
    snapshot = classify_checkpoint_document(
        _checkpoint(task_run_directive=raw, include_directive=True)
    )

    assert snapshot.status is TaskContinuityStatus.INVALID
    assert snapshot.resumable is False


def test_nonterminal_plan_with_executable_checkpoint_plan_is_invalid() -> None:
    directive = TaskRunDirective(TaskDirective.PLAN, DeliberationProfile.NORMAL, "preview")

    snapshot = classify_checkpoint_document(
        _checkpoint(
            objective=directive.canonical_objective(),
            task_run_directive=directive.to_checkpoint_dict(),
            include_directive=True,
            plan=_plan(),
        )
    )

    assert snapshot.status is TaskContinuityStatus.INVALID
    assert snapshot.resumable is False


def test_terminal_plan_retains_w10_terminal_classification() -> None:
    directive = TaskRunDirective(TaskDirective.PLAN, DeliberationProfile.NORMAL, "preview")

    snapshot = classify_checkpoint_document(
        _checkpoint(
            objective=directive.canonical_objective(),
            task_run_directive=directive.to_checkpoint_dict(),
            include_directive=True,
            plan=_plan(),
            terminal_disposition="complete",
        )
    )

    assert snapshot.status is TaskContinuityStatus.TERMINAL
    assert snapshot.resumable is False


def _write_checkpoint(home: Path, workspace: Path, payload: dict[str, object]) -> Path:
    workspace.mkdir()
    paths = AppPaths.discover(home, env={})
    workspace_paths = paths.for_workspace(WorkspaceContext.create(workspace).workspace_id)
    workspace_paths.ensure_directories()
    workspace_paths.checkpoint_file.write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )
    return workspace_paths.checkpoint_file


@pytest.mark.parametrize(
    "payload",
    [
        _checkpoint(
            task_run_directive={
                "schema_version": 1,
                "directive": "read",
                "deliberation_profile": "normal",
                "subject": "old W10 objective",
                "extra": "reject",
            },
            include_directive=True,
        ),
        _checkpoint(
            objective=TaskRunDirective(TaskDirective.PLAN, DeliberationProfile.NORMAL, "preview").canonical_objective(),
            task_run_directive=TaskRunDirective(
                TaskDirective.PLAN,
                DeliberationProfile.NORMAL,
                "preview",
            ).to_checkpoint_dict(),
            include_directive=True,
            plan=_plan(),
        ),
    ],
)
def test_continue_preflight_rejects_invalid_w11_checkpoint_without_application(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    payload: dict[str, object],
) -> None:
    checkpoint = _write_checkpoint(tmp_path / "home", tmp_path / "workspace", payload)
    before = checkpoint.read_bytes()
    created: list[Any] = []

    def forbidden(*_args: object, **_kwargs: object) -> Any:
        created.append(True)
        raise AssertionError("invalid /continue must not create an application")

    monkeypatch.setattr(cli, "_create_application", forbidden)

    result = cli.main(
        [
            "run",
            "--json",
            "--home",
            str(tmp_path / "home"),
            "--workspace",
            str(tmp_path / "workspace"),
            "/continue",
        ]
    )

    document = json.loads(capsys.readouterr().out)
    assert result == 2
    assert document["success"] is False
    assert document["reason_code"] == "CHECKPOINT_INVALID"
    assert created == []
    assert checkpoint.read_bytes() == before

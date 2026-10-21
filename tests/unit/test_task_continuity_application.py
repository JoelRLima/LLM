"""Parity and isolation evidence for the task-continuity Application query."""

from __future__ import annotations

import json
import operator
from collections.abc import Mapping
from dataclasses import fields
from pathlib import Path
from typing import Any

import pytest

import llm_agent.application.task_continuity as continuity
from llm_agent.agent.continuity.service import TaskContinuityService
from llm_agent.application.context import AppPaths, WorkspaceContext
from llm_agent.application.task_continuity import TaskContinuityRequest, read_task_continuity

DOCUMENT_FIELDS = {
    "schema_version",
    "workspace_id",
    "status",
    "reason_code",
    "reason",
    "resumable",
    "checkpoint_present",
    "checkpoint_schema_version",
    "objective_preview",
    "root_task_id",
    "task_definition_ref",
    "terminal_disposition",
    "hierarchical_status",
    "plan_progress",
    "continuity",
    "resume_generation",
    "related_runs",
}


def _checkpoint(*, definition_state: str = "complete") -> dict[str, object]:
    task_definition: dict[str, object] = {
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
    return {
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


@pytest.mark.parametrize(
    ("case", "expected_status"),
    [
        ("absent", "absent"),
        ("resumable", "resumable"),
        ("paused", "paused"),
        ("invalid", "invalid"),
        ("unsupported", "unsupported"),
    ],
)
def test_application_projection_matches_agent_oracle_without_writes(
    tmp_path: Path, case: str, expected_status: str
) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app_paths = AppPaths.discover(home, env={})
    workspace_id = WorkspaceContext.create(workspace).workspace_id
    workspace_paths = app_paths.for_workspace(workspace_id)
    checkpoint = workspace_paths.checkpoint_file
    if case != "absent":
        checkpoint.parent.mkdir(parents=True)
        if case == "invalid":
            checkpoint.write_text("{corrupt", encoding="utf-8")
        else:
            payload = _checkpoint(definition_state="contract_ready" if case == "unsupported" else "complete")
            if case == "paused":
                payload["continuity"] = {
                    "schema_version": 1,
                    "resume_generation": 2,
                    "last_run_id": "run-1",
                    "interrupted": True,
                    "interruption_reason": "keyboard_interrupt",
                    "interrupted_at": "2026-09-02T12:00:00Z",
                }
            checkpoint.write_text(json.dumps(payload), encoding="utf-8")
    before = checkpoint.read_bytes() if checkpoint.exists() else None
    request = TaskContinuityRequest(app_paths=app_paths, workspace=workspace)

    agent_document = TaskContinuityService(workspace_paths).snapshot().to_dict()
    result = read_task_continuity(request)
    document = result.to_dict()

    assert document == agent_document
    assert set(document) == DOCUMENT_FIELDS
    assert result.status == document["status"] == expected_status
    assert result.reason_code == document["reason_code"]
    assert result.resumable is document["resumable"]
    assert document["workspace_id"] == workspace_id
    assert read_task_continuity(request).to_dict() == document
    assert (checkpoint.read_bytes() if checkpoint.exists() else None) == before
    if case == "absent":
        assert not home.exists()
        assert not workspace_paths.state_dir.exists()


def test_application_result_is_deeply_isolated_from_returned_documents(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    request = TaskContinuityRequest(app_paths=AppPaths.discover(tmp_path / "home", env={}), workspace=workspace)
    result = read_task_continuity(request)
    first = result.to_dict()
    expected = result.to_dict()
    plan_progress = first["plan_progress"]
    assert isinstance(plan_progress, dict)
    plan_progress["total_steps"] = 999
    related_runs = first["related_runs"]
    assert isinstance(related_runs, list)
    related_runs.append({"run_id": "forged"})

    assert result.to_dict() == expected
    assert {field.name for field in fields(result)} == {
        "status", "reason_code", "resumable", "_document"
    }
    assert isinstance(result._document, Mapping)
    with pytest.raises(TypeError):
        operator.setitem(result._document, "status", "forged")


def test_application_queries_one_canonical_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app_paths = AppPaths.discover(tmp_path / "home", env={})
    expected_paths = app_paths.for_workspace(WorkspaceContext.create(workspace).workspace_id)
    observed: list[Path] = []
    reads: list[bool] = []

    class CountingService:
        def __init__(self, workspace_paths: Any) -> None:
            observed.append(workspace_paths.checkpoint_file)
            self.delegate = TaskContinuityService(workspace_paths)

        def snapshot(self) -> Any:
            reads.append(True)
            return self.delegate.snapshot()

    monkeypatch.setattr(continuity, "TaskContinuityService", CountingService)

    result = read_task_continuity(TaskContinuityRequest(app_paths=app_paths, workspace=workspace))

    assert result.status == "absent"
    assert observed == [expected_paths.checkpoint_file]
    assert reads == [True]
    assert not expected_paths.state_dir.exists()

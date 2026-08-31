from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from agent.runtime.paths import WorkspacePaths
from agent.task_definition.models import TaskContract, TaskSpec, TaskSpecPhase
from agent.task_definition.repository import TaskDefinitionRepository


@pytest.fixture
def workspace_paths(tmp_path: Path) -> WorkspacePaths:
    paths = WorkspacePaths(
        workspace_id="workspace-test",
        data_dir=tmp_path / "data",
        state_dir=tmp_path / "state",
        cache_dir=tmp_path / "cache",
    )
    paths.ensure_directories()
    return paths


@pytest.fixture
def repository(workspace_paths: WorkspacePaths) -> TaskDefinitionRepository:
    return TaskDefinitionRepository(workspace_paths)


def make_contract(
    task_id: str = "task-1",
    objective: str = "Do the thing",
    **overrides: Any,
) -> TaskContract:
    values: dict[str, Any] = {
        "task_id": task_id,
        "objective": objective,
        "summary": "A bounded task",
        "requirements": ("one", "two"),
        "constraints": ("no external side effects",),
        "invariants": ("preserve authority",),
        "success_criteria": ("evidence is available",),
        "out_of_scope": ("unrelated work",),
        "stop_conditions": ("required evidence is unavailable",),
        "assumptions": ("workspace is available",),
        "open_questions": (),
    }
    values.update(overrides)
    return TaskContract(**values)


def make_phase(
    phase_id: str = "phase-1",
    *,
    depends_on: tuple[str, ...] = (),
) -> TaskSpecPhase:
    return TaskSpecPhase(
        phase_id=phase_id,
        title=f"Phase {phase_id}",
        goal=f"Complete {phase_id}",
        requirements=("read the relevant inputs",),
        invariants=("do not bypass the authority binding",),
        acceptance_criteria=("record bounded evidence",),
        evidence_requirements=("test output",),
        depends_on=depends_on,
    )


def make_spec(
    contract: TaskContract,
    phases: tuple[TaskSpecPhase, ...] | None = None,
    **overrides: Any,
) -> TaskSpec:
    values: dict[str, Any] = {
        "task_id": contract.task_id,
        "contract_version": contract.version,
        "contract_digest": contract.digest(),
        "phases": phases or (make_phase(),),
        "architecture": "Descriptive specification above the executable Plan.",
        "global_requirements": ("use the existing planning owner",),
        "global_invariants": ("do not turn phases into executable tool calls",),
        "global_acceptance": ("all required checks pass",),
    }
    values.update(overrides)
    return TaskSpec(**values)

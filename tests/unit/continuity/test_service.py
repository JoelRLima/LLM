import json
from types import SimpleNamespace

import pytest

from agent.checkpoint_manager import CheckpointLoadError, CheckpointManager
from agent.continuity.models import MAX_OBJECTIVE_PREVIEW, MAX_REASON, MAX_RELATED_RUNS, TaskContinuityStatus
from agent.continuity.service import (
    REASON_CHECKPOINT_INVALID_CONTINUITY,
    REASON_CHECKPOINT_ROOT_MISSING,
    REASON_HIERARCHICAL_RESUME_UNSUPPORTED,
    REASON_TASK_DEFINITION_BINDING_MISSING,
    REASON_TASK_DEFINITION_INCOMPLETE,
    TaskContinuityService,
)


def _checkpoint(
    *, objective: str = "continuar", definition_state: str = "complete", **overrides: object
) -> dict[str, object]:
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
    payload: dict[str, object] = {
        "schema_version": 2,
        "objective": objective,
        "root_task_id": "root-task",
        "task_definition": task_definition,
        "plan": [
            {"tool": "echo", "args": {}, "_step_id": "step-1"},
            {"tool": "echo", "args": {}, "_step_id": "step-2"},
        ],
        "plan_step": 1,
        "current_step_id": "step-2",
        "step_records": [
            {"step_id": "step-1", "status": "completed", "attempts": 1, "last_error": ""},
            {"step_id": "step-2", "status": "pending", "attempts": 0, "last_error": ""},
        ],
        "requested_effects": [],
        "executed_effects": [],
        "waived_effects": [],
        "prohibited_effects": [],
    }
    payload.update(overrides)
    return payload


@pytest.mark.parametrize(
    ("definition_state", "expected_status", "expected_resumable", "expected_reason"),
    [
        ("complete", TaskContinuityStatus.RESUMABLE, True, "CHECKPOINT_RESUMABLE"),
        ("contract_ready", TaskContinuityStatus.UNSUPPORTED, False, REASON_TASK_DEFINITION_INCOMPLETE),
    ],
)
def test_definition_state_controls_resume_classification_and_preserves_checkpoint(
    tmp_path, definition_state, expected_status, expected_resumable, expected_reason
) -> None:
    path = tmp_path / f"checkpoint-{definition_state}.json"
    _write_checkpoint(path, _checkpoint(definition_state=definition_state))
    before = path.read_bytes()

    snapshot = TaskContinuityService(_paths(path)).snapshot()

    assert snapshot.status is expected_status
    assert snapshot.resumable is expected_resumable
    assert snapshot.reason_code == expected_reason
    assert path.read_bytes() == before


def _paths(path) -> SimpleNamespace:
    return SimpleNamespace(workspace_id="workspace-test", checkpoint_file=path)


def _write_checkpoint(path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_status_distinguishes_absence_from_a_valid_legacy_schema_2_checkpoint(tmp_path) -> None:
    path = tmp_path / "checkpoint.json"
    service = TaskContinuityService(_paths(path))

    absent = service.snapshot()
    assert absent.status is TaskContinuityStatus.ABSENT
    assert absent.resumable is False
    assert absent.checkpoint_present is False

    _write_checkpoint(path, _checkpoint())
    resumable = service.snapshot()
    assert resumable.status is TaskContinuityStatus.RESUMABLE
    assert resumable.resumable is True
    assert resumable.checkpoint_schema_version == 2
    assert resumable.continuity is None
    assert resumable.objective_preview == "continuar"
    assert resumable.plan_progress.completed_steps == 1
    assert resumable.plan_progress.total_steps == 2


def test_status_classifies_pause_terminal_and_hierarchical_running_deterministically(tmp_path) -> None:
    path = tmp_path / "checkpoint.json"
    service = TaskContinuityService(_paths(path))
    continuity = {
        "schema_version": 1,
        "resume_generation": 2,
        "last_run_id": "run-1",
        "interrupted": True,
        "interruption_reason": "keyboard_interrupt",
        "interrupted_at": "2026-09-02T12:00:00Z",
    }

    _write_checkpoint(path, _checkpoint(continuity=continuity))
    paused = service.status()
    assert paused.status is TaskContinuityStatus.PAUSED
    assert paused.resumable is True
    assert paused.resume_generation == 2

    _write_checkpoint(path, _checkpoint(terminal_disposition="cancelled", continuity=continuity))
    terminal = service.snapshot()
    assert terminal.status is TaskContinuityStatus.TERMINAL
    assert terminal.resumable is False
    assert terminal.reason_code == "TASK_ALREADY_TERMINAL"

    _write_checkpoint(path, _checkpoint(hierarchical_lifecycle={"status": "running"}))
    unsupported = service.classify()
    assert unsupported.status is TaskContinuityStatus.UNSUPPORTED
    assert unsupported.resumable is False
    assert unsupported.reason_code == REASON_HIERARCHICAL_RESUME_UNSUPPORTED


@pytest.mark.parametrize(
    ("missing_field", "reason_code"),
    [
        ("root_task_id", REASON_CHECKPOINT_ROOT_MISSING),
        ("task_definition", REASON_TASK_DEFINITION_BINDING_MISSING),
    ],
)
def test_status_never_claims_resumable_for_unbound_checkpoint(
    tmp_path, missing_field: str, reason_code: str
) -> None:
    payload = _checkpoint()
    payload.pop(missing_field)
    path = tmp_path / f"checkpoint-{missing_field}.json"
    _write_checkpoint(path, payload)
    before = path.read_bytes()

    snapshot = TaskContinuityService(_paths(path)).snapshot()

    assert snapshot.status is TaskContinuityStatus.INVALID
    assert snapshot.resumable is False
    assert snapshot.reason_code == reason_code
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "continuity",
    [
        {"schema_version": 1, "resume_generation": -1, "last_run_id": None, "interrupted": False},
        {
            "schema_version": 1,
            "resume_generation": 0,
            "last_run_id": None,
            "interrupted": False,
            "interruption_reason": "stale",
        },
        {
            "schema_version": 1,
            "resume_generation": 0,
            "last_run_id": None,
            "interrupted": True,
            "interrupted_at": "not-a-timestamp",
        },
        {
            "schema_version": 1,
            "resume_generation": 0,
            "last_run_id": None,
            "interrupted": False,
            "unknown": True,
        },
    ],
)
def test_malformed_continuity_is_invalid_and_checkpoint_is_preserved(tmp_path, continuity) -> None:
    path = tmp_path / "checkpoint.json"
    _write_checkpoint(path, _checkpoint(continuity=continuity))
    before = path.read_bytes()

    snapshot = TaskContinuityService(_paths(path)).snapshot()

    assert snapshot.status is TaskContinuityStatus.INVALID
    assert snapshot.resumable is False
    assert snapshot.reason_code == REASON_CHECKPOINT_INVALID_CONTINUITY
    assert path.read_bytes() == before


def test_corrupt_checkpoint_fails_closed_with_bounded_reason_and_preserves_bytes(tmp_path) -> None:
    path = tmp_path / "checkpoint.json"
    path.write_text("{" + "x" * 20_000, encoding="utf-8")
    before = path.read_bytes()

    snapshot = TaskContinuityService(_paths(path)).snapshot()

    assert snapshot.status is TaskContinuityStatus.INVALID
    assert snapshot.resumable is False
    assert snapshot.reason_code == "CHECKPOINT_CORRUPT"
    assert len(snapshot.reason) <= MAX_REASON
    assert path.read_bytes() == before


def test_inconsistent_plan_records_are_invalid_and_preserved(tmp_path) -> None:
    path = tmp_path / "checkpoint.json"
    payload = _checkpoint()
    records = payload["step_records"]
    assert isinstance(records, list)
    records[1] = {**records[1], "step_id": "forged-step"}
    _write_checkpoint(path, payload)
    before = path.read_bytes()

    snapshot = TaskContinuityService(_paths(path)).snapshot()

    assert snapshot.status is TaskContinuityStatus.INVALID
    assert snapshot.reason_code == "CHECKPOINT_INVALID"
    assert path.read_bytes() == before


def test_projection_bounds_untrusted_objective_and_uses_checkpoint_manager_only() -> None:
    payload = _checkpoint(objective="objetivo-" + "x" * 10_000)

    class ReadOnlyManager:
        def __init__(self) -> None:
            self.load_calls = 0

        def load(self):
            self.load_calls += 1
            return payload

        def save(self, *_args, **_kwargs):
            raise AssertionError("continuity status must not save")

        def delete(self, *_args, **_kwargs):
            raise AssertionError("continuity status must not delete")

    manager = ReadOnlyManager()
    snapshot = TaskContinuityService(
        SimpleNamespace(workspace_id="workspace-test"),
        checkpoint_manager=manager,
    ).snapshot()

    assert manager.load_calls == 1
    assert snapshot.status is TaskContinuityStatus.RESUMABLE
    assert len(snapshot.objective_preview) <= MAX_OBJECTIVE_PREVIEW
    assert len(snapshot.reason) <= MAX_REASON
    assert len(snapshot.related_runs) <= MAX_RELATED_RUNS


def test_checkpoint_manager_reports_optional_continuity_schema_errors(tmp_path) -> None:
    path = tmp_path / "checkpoint.json"
    _write_checkpoint(
        path,
        _checkpoint(
            continuity={
                "schema_version": 99,
                "resume_generation": 0,
                "last_run_id": None,
                "interrupted": False,
            }
        ),
    )

    with pytest.raises(CheckpointLoadError) as failure:
        CheckpointManager(path).load()

    assert failure.value.reason_code == "CHECKPOINT_INCOMPATIBLE_CONTINUITY_SCHEMA"

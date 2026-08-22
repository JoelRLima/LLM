import json

import pytest

from agent.checkpoint_manager import (
    CHECKPOINT_SCHEMA_VERSION,
    CheckpointLoadError,
    CheckpointManager,
)


class _State:
    def to_checkpoint_dict(self):
        return {
            "objective": "analisar",
            "plan": [{"tool": "echo", "args": {}, "_step_id": "step-1"}],
            "step_records": [
                {
                    "step_id": "step-1",
                    "status": "pending",
                    "attempts": 0,
                    "last_error": "",
                }
            ],
            "requested_effects": [],
            "executed_effects": [],
            "waived_effects": [],
            "prohibited_effects": [],
        }


def test_checkpoint_round_trip_is_versioned(tmp_path):
    path = tmp_path / "nested" / "checkpoint.json"
    manager = CheckpointManager(str(path))

    manager.save(_State())

    data = manager.load()
    assert data is not None
    assert data["schema_version"] == CHECKPOINT_SCHEMA_VERSION
    assert data["objective"] == "analisar"


def test_checkpoint_rejects_incompatible_version(tmp_path):
    path = tmp_path / "checkpoint.json"
    path.write_text(
        json.dumps({"schema_version": 999, "objective": "x", "plan": []}),
        encoding="utf-8",
    )

    with pytest.raises(CheckpointLoadError) as failure:
        CheckpointManager(str(path)).load()
    assert failure.value.reason_code == "CHECKPOINT_INCOMPATIBLE_SCHEMA"


def test_checkpoint_rejects_legacy_file_without_version(tmp_path):
    path = tmp_path / "checkpoint.json"
    path.write_text(json.dumps({"objective": "x", "plan": []}), encoding="utf-8")

    with pytest.raises(CheckpointLoadError):
        CheckpointManager(str(path)).load()


def test_checkpoint_rejects_schema_v1_instead_of_inferring_progress(tmp_path):
    path = tmp_path / "checkpoint.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "objective": "efeito colateral",
                "plan": [{"tool": "file_writer", "args": {}}],
                "plan_step": 1,
                "tool_history": [],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(CheckpointLoadError) as failure:
        CheckpointManager(str(path)).load()
    assert failure.value.reason_code == "CHECKPOINT_INCOMPATIBLE_SCHEMA"


def test_checkpoint_rejects_malformed_plan(tmp_path):
    path = tmp_path / "checkpoint.json"
    path.write_text(
        json.dumps({"schema_version": CHECKPOINT_SCHEMA_VERSION, "objective": "x", "plan": ["bad"]}),
        encoding="utf-8",
    )

    with pytest.raises(CheckpointLoadError):
        CheckpointManager(str(path)).load()


def test_checkpoint_absence_is_distinct_from_invalid_state(tmp_path):
    assert CheckpointManager(str(tmp_path / "missing.json")).load() is None


def test_checkpoint_rejects_ambiguous_v2_migration(tmp_path):
    path = tmp_path / "checkpoint.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": CHECKPOINT_SCHEMA_VERSION,
                "objective": "escreva",
                "plan": [],
                "step_records": [],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(CheckpointLoadError) as failure:
        CheckpointManager(str(path)).load()
    assert failure.value.reason_code == "CHECKPOINT_MIGRATION_AMBIGUOUS"


def test_checkpoint_rejects_invalid_step_status_instead_of_requeuing(tmp_path):
    path = tmp_path / "checkpoint.json"
    payload = _State().to_checkpoint_dict()
    payload["schema_version"] = CHECKPOINT_SCHEMA_VERSION
    payload["step_records"][0]["status"] = "invented"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(CheckpointLoadError, match="status de passo"):
        CheckpointManager(str(path)).load()

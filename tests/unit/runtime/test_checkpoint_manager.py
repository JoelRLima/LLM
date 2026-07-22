import json

from agent.checkpoint_manager import CHECKPOINT_SCHEMA_VERSION, CheckpointManager


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

    assert CheckpointManager(str(path)).load() is None


def test_checkpoint_rejects_legacy_file_without_version(tmp_path):
    path = tmp_path / "checkpoint.json"
    path.write_text(json.dumps({"objective": "x", "plan": []}), encoding="utf-8")

    assert CheckpointManager(str(path)).load() is None


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

    assert CheckpointManager(str(path)).load() is None


def test_checkpoint_rejects_malformed_plan(tmp_path):
    path = tmp_path / "checkpoint.json"
    path.write_text(
        json.dumps({"schema_version": CHECKPOINT_SCHEMA_VERSION, "objective": "x", "plan": ["bad"]}),
        encoding="utf-8",
    )

    assert CheckpointManager(str(path)).load() is None

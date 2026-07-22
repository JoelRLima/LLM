import json

from agent.health import state_checks
from agent.health.core import EXPECTED_MEMORY_SECTIONS, STATUS_OK
from agent.health.runtime_checks import check_skills


def test_health_check_uses_the_echo_skill_public_contract() -> None:
    result = check_skills()

    assert result.status == STATUS_OK
    assert result.details["echo_test_result"]["ok"] is True


def test_memory_health_matches_json_and_sqlite_responsibilities(tmp_path, monkeypatch) -> None:
    memory_path = tmp_path / "agent_memory.json"
    memory_path.write_text(
        json.dumps({section: {} for section in EXPECTED_MEMORY_SECTIONS}),
        encoding="utf-8",
    )
    monkeypatch.setattr(state_checks, "MEMORY_PATH", memory_path)
    monkeypatch.setattr(state_checks, "check_memory_backups", lambda: {
        "dir_exists": False,
        "total_backups": 0,
        "valid_files": [],
        "invalid_files": [],
    })

    result = state_checks.check_memory()

    assert result.status == STATUS_OK
    assert "key_findings" not in EXPECTED_MEMORY_SECTIONS
    assert "file_summaries" not in EXPECTED_MEMORY_SECTIONS

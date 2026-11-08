from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from llm_agent.agent.planning.requirement_verification import (
    _confined_regular_file,
    _UnsafePath,
    verify_requirement_checks,
)
from llm_agent.agent.task_definition.verification_model import VerificationCheck, VerificationKind


def _check(kind: VerificationKind, path: str, fields: tuple[str, ...] = ()) -> VerificationCheck:
    return VerificationCheck("check_1", kind, "requirements", 0, path, fields)


def test_missing_and_regular_file_checks(tmp_path: Path) -> None:
    (tmp_path / "present.txt").write_text("private content", encoding="utf-8")
    missing, present = verify_requirement_checks(
        (_check("file_exists", "missing.txt"), _check("file_exists", "present.txt")), tmp_path
    )
    assert missing.status == "violated" and missing.reason_code == "missing_file"
    assert present.status == "satisfied" and present.evidence_digest is None
    assert present.observed_at.endswith("Z")


@pytest.mark.parametrize(
    ("content", "status", "reason"),
    [
        ('{"a": 1}', "satisfied", "required_fields_present"),
        ('{"b": 1}', "violated", "required_fields_missing"),
        ('{"a": ', "violated", "invalid_json"),
        ('[]', "violated", "json_not_object"),
        ('{"a": 1, "a": 2}', "violated", "invalid_json"),
        ('{"a": NaN}', "violated", "invalid_json"),
    ],
)
def test_json_shape_checks(tmp_path: Path, content: str, status: str, reason: str) -> None:
    target = tmp_path / "data.json"
    target.write_text(content, encoding="utf-8")
    result, = verify_requirement_checks((_check("json_fields", "data.json", ("a",)),), tmp_path)
    assert result.status == status
    assert result.reason_code == reason
    if reason != "invalid_json":
        assert result.evidence_digest == hashlib.sha256(content.encode()).hexdigest()


def test_json_size_limit_and_fresh_digest(tmp_path: Path) -> None:
    target = tmp_path / "data.json"
    target.write_text('{"a":1}', encoding="utf-8")
    check = _check("json_fields", "data.json", ("a",))
    before, = verify_requirement_checks((check,), tmp_path)
    target.write_text('{"a":2}', encoding="utf-8")
    after, = verify_requirement_checks((check,), tmp_path)
    assert before.evidence_digest != after.evidence_digest
    target.write_bytes(b" " * (1024 * 1024 + 1))
    oversized, = verify_requirement_checks((check,), tmp_path)
    assert oversized.status == "inconclusive" and oversized.reason_code == "file_too_large"


def test_deep_json_and_unavailable_workspace_are_observations(tmp_path: Path) -> None:
    target = tmp_path / "deep.json"
    target.write_text("[" * 12000 + "0" + "]" * 12000, encoding="utf-8")
    check = _check("json_fields", "deep.json", ("a",))
    result, = verify_requirement_checks((check,), tmp_path)
    assert result.status == "violated" and result.reason_code == "invalid_json"
    unavailable, = verify_requirement_checks((check,), tmp_path / "absent")
    assert unavailable.status == "inconclusive"
    json.dumps(unavailable.__dict__)


def test_rejects_paths_directories_and_symlink_escape(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside.txt"
    outside.write_text("outside", encoding="utf-8")
    try:
        (tmp_path / "link.txt").symlink_to(outside)
    except (OSError, NotImplementedError):
        pass
    checks = (
        _check("file_exists", "folder"),
        _check("file_exists", "link.txt"),
    )
    (tmp_path / "folder").mkdir()
    results = verify_requirement_checks(checks, tmp_path)
    assert results[0].status == "inconclusive"
    if (tmp_path / "link.txt").is_symlink():
        assert results[1].status == "inconclusive"


@pytest.mark.parametrize("path", ("C:/outside.json", "result.json:stream", "./result.json", "a//b"))
def test_low_level_path_guard_rejects_nonportable_components(tmp_path: Path, path: str) -> None:
    with pytest.raises(_UnsafePath):
        _confined_regular_file(tmp_path, path)

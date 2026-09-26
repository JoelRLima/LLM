"""Equivalence tests for the migrated W18, W19 and W20 checker seams."""

from __future__ import annotations

import shutil
from pathlib import Path

from scripts import check_wave18_architecture as wave18
from scripts import check_wave19_architecture as wave19
from scripts import check_wave20_architecture as wave20

ROOT = Path(__file__).resolve().parents[3]


def _copy_repository(source: Path, destination: Path) -> None:
    ignored = shutil.ignore_patterns(".git", ".tmp", "__pycache__", ".pytest_cache")
    shutil.copytree(source, destination, ignore=ignored)


def test_current_repository_remains_green_for_all_migrated_checkers(tmp_path: Path) -> None:
    fixture = tmp_path / "current"
    _copy_repository(ROOT, fixture)
    assert wave18.check_architecture(fixture) == []
    assert wave19.check_architecture(fixture) == []
    assert wave20.findings(fixture) == []


def test_w18_negative_fixture_preserves_installer_boundary_rule(tmp_path: Path) -> None:
    fixture = tmp_path / "w18"
    _copy_repository(ROOT, fixture)
    path = fixture / "agent/runtime/injected.py"
    path.write_text("from installer import install\n", encoding="utf-8")
    findings = wave18.check_architecture(fixture)
    assert any(item.rule_id == "W18-ARCH-04" for item in findings)


def test_w18_positive_fixture_accepts_a_safe_runtime_module(tmp_path: Path) -> None:
    fixture = tmp_path / "w18-positive"
    _copy_repository(ROOT, fixture)
    (fixture / "agent/runtime/valid_fixture.py").write_text(
        "from agent.runtime.paths import AppPaths\n\nVALID_FIXTURE = AppPaths\n",
        encoding="utf-8",
    )
    assert wave18.check_architecture(fixture) == []


def test_w19_negative_fixture_preserves_legacy_path_rule(tmp_path: Path) -> None:
    fixture = tmp_path / "w19"
    _copy_repository(ROOT, fixture)
    path = fixture / "agent/fixture.py"
    path.write_text("from agent.runtime.paths import RUNTIME_DIR\n", encoding="utf-8")
    findings = wave19.check_architecture(fixture)
    assert any(item.rule_id == "W19-S05-002" for item in findings)


def test_w19_positive_fixture_accepts_typed_path_surface(tmp_path: Path) -> None:
    fixture = tmp_path / "w19-positive"
    _copy_repository(ROOT, fixture)
    (fixture / "agent/typed_fixture.py").write_text(
        "from agent.runtime.paths import AppPaths\n\nTYPED_PATHS = AppPaths\n",
        encoding="utf-8",
    )
    assert wave19.check_architecture(fixture) == []


def test_w20_positive_and_negative_boundary_fixtures(tmp_path: Path) -> None:
    positive = tmp_path / "w20-positive"
    (positive / "agent/engineering").mkdir(parents=True)
    (positive / "agent/engineering/__init__.py").write_text("", encoding="utf-8")
    (positive / "agent/engineering/valid.py").write_text(
        "from agent.runtime.paths import AppPaths\n",
        encoding="utf-8",
    )
    assert wave20.findings(positive) == []

    negative = tmp_path / "w20-negative"
    (negative / "agent/engineering").mkdir(parents=True)
    (negative / "agent/engineering/__init__.py").write_text("", encoding="utf-8")
    (negative / "agent/engineering/invalid.py").write_text(
        "from agent.planning import policy\n",
        encoding="utf-8",
    )
    assert any(item.startswith("W20-A-FORBIDDEN-IMPORT") for item in wave20.findings(negative))

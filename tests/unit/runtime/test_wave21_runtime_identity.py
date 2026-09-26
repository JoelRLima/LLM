"""Deterministic tests for the separate runtime identity layer."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

from scripts.check_w21_runtime_identity import check_identity


def _registry() -> dict[str, object]:
    return {
        "bridges": [
            {
                "bridge_id": "IDENTITY-001",
                "class": "compatibility",
                "surface": "agent.fixture_legacy.symbols.Symbol",
                "target_owner": "agent.fixture_canonical.symbols.Symbol",
                "lane": "A",
            }
        ]
    }


def test_pending_bridges_are_reported_without_runtime_imports() -> None:
    report = check_identity(_registry())
    assert report == {
        "checked": [],
        "pending": ["IDENTITY-001"],
        "failures": [],
        "passed": True,
    }


def test_active_fixture_checks_actual_object_identity(tmp_path: Path, monkeypatch) -> None:
    package = tmp_path / "agent"
    (package / "fixture_legacy").mkdir(parents=True)
    (package / "fixture_canonical").mkdir(parents=True)
    for relative in ("__init__.py", "fixture_legacy/__init__.py", "fixture_canonical/__init__.py"):
        (package / relative).write_text("", encoding="utf-8")
    (package / "fixture_canonical/symbols.py").write_text("class Symbol: pass\n", encoding="utf-8")
    (package / "fixture_legacy/symbols.py").write_text(
        "from agent.fixture_canonical.symbols import Symbol\n",
        encoding="utf-8",
    )
    for module_name in tuple(sys.modules):
        if module_name == "agent" or module_name.startswith("agent."):
            monkeypatch.delitem(sys.modules, module_name, raising=False)
    monkeypatch.syspath_prepend(str(tmp_path))
    importlib.invalidate_caches()
    registry = _registry()
    report = check_identity(registry, activated_lanes=("A",))
    assert report == {
        "checked": ["IDENTITY-001"],
        "pending": [],
        "failures": [],
        "passed": True,
    }

"""Focused seam tests for the structural analyzer and existing lifecycle."""

from __future__ import annotations

from pathlib import Path

from scripts.check_w21_compatibility import (
    BridgeLifecycle,
    analyze_registered_bridges,
    check_compatibility,
)


def _write(root: Path, source: str) -> None:
    path = root / "agent" / "skills" / "facade.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    (root / "agent" / "__init__.py").write_text("", encoding="utf-8")
    (root / "agent" / "skills" / "__init__.py").write_text("", encoding="utf-8")
    path.write_text(source, encoding="utf-8")


def _registry() -> dict[str, object]:
    return {
        "bridges": [
            {
                "bridge_id": "TEST-SYMBOL-BRIDGE",
                "class": "compatibility",
                "surface": "agent.skills.facade.SkillSpec",
                "target_owner": "agent.operation.spec.SkillSpec",
                "lane": "Z",
            }
        ]
    }


def _result(root: Path, source: str, **kwargs):
    _write(root, source)
    return analyze_registered_bridges(root, registry=_registry(), **kwargs)[0]


def test_registered_exact_import_reaches_existing_lifecycle(tmp_path: Path) -> None:
    result = _result(tmp_path, "from agent.operation.spec import SkillSpec\n")
    assert result.analysis.state == "EXACT_SYMBOL"
    assert result.lifecycle is BridgeLifecycle.EXACT_TARGET


def test_wrong_import_is_deferred_only_for_unactivated_transition(tmp_path: Path) -> None:
    result = _result(
        tmp_path,
        "from agent.operation.catalog import SkillSpec\n",
        mode="transition",
    )
    assert result.analysis.state == "INVALID"
    assert result.lifecycle is BridgeLifecycle.PENDING_MIGRATION
    assert check_compatibility(
        tmp_path,
        registry=_registry(),
        policy={},
        baseline={},
        mode="transition",
        activated_lanes=("Z",),
    )[0].code == "W21-COMP-WRONG-SYMBOL-BRIDGE"


def test_competing_module_binding_is_not_exact(tmp_path: Path) -> None:
    source = (
        "from agent.operation.spec import SkillSpec\n"
        "if enabled:\n"
        "    from agent.operation.spec import SkillSpec\n"
    )
    result = _result(tmp_path, source, mode="strict")
    assert result.analysis.state == "AMBIGUOUS"
    assert result.lifecycle is BridgeLifecycle.VIOLATION


def test_function_and_class_locals_do_not_compete(tmp_path: Path) -> None:
    source = (
        "from agent.operation.spec import SkillSpec\n"
        "def helper():\n"
        "    SkillSpec = replacement\n"
        "class Holder:\n"
        "    SkillSpec = replacement\n"
    )
    result = _result(tmp_path, source, mode="strict")
    assert result.analysis.state == "EXACT_SYMBOL"
    assert result.lifecycle is BridgeLifecycle.EXACT_TARGET


def test_unsupported_indirect_bridge_is_rejected_in_strict_mode(tmp_path: Path) -> None:
    source = (
        "import agent.operation.spec as spec\n"
        "SkillSpec = spec.SkillSpec\n"
    )
    result = _result(tmp_path, source, mode="strict")
    assert result.analysis.state == "INVALID"
    assert result.lifecycle is BridgeLifecycle.VIOLATION

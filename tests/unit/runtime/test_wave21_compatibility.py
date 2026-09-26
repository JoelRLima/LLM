"""Remote-safe exact compatibility and closed-world legacy-path tests."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from scripts.check_w21_compatibility import (
    BridgeLifecycle,
    analyze_registered_bridges,
    check_compatibility,
    check_source_text,
    is_registered_bridge,
    validate_bridge_edge,
)
from scripts.w21_architecture import RepositorySource
from scripts.w21_architecture.compatibility import legacy_consumers

ROOT = Path(__file__).resolve().parents[3]


def _registry() -> dict[str, object]:
    return json.loads((ROOT / "quality/architecture_compatibility.json").read_text(encoding="utf-8"))


def _write(root: Path, relative: str, text: str = "") -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _copy_projections(root: Path) -> None:
    quality = root / "quality"
    quality.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "quality/architecture_policy.json", quality / "architecture_policy.json")
    shutil.copy2(ROOT / "quality/architecture_baseline.json", quality / "architecture_baseline.json")


def _symbol_registry() -> dict[str, object]:
    return {
        "bridges": [
            {
                "bridge_id": "TEST-SYMBOL-BRIDGE",
                "class": "compatibility",
                "surface": "agent.skills.facade.SkillSpec",
                "target_owner": "agent.operation.spec.SkillSpec",
            }
        ]
    }


def _lifecycle_registry(*, lane: str = "Z") -> dict[str, object]:
    registry = _symbol_registry()
    bridge = registry["bridges"][0]
    assert isinstance(bridge, dict)
    bridge["lane"] = lane
    return registry


def _module_registry() -> dict[str, object]:
    return {
        "bridges": [
            {
                "bridge_id": "TEST-MODULE-BRIDGE",
                "class": "compatibility",
                "surface": "agent.tools.process_tree",
                "target_owner": "agent.process.tree",
            }
        ]
    }


def _symbol_fixture(root: Path, source: str) -> list[object]:
    _write(root, "agent/__init__.py")
    _write(root, "agent/skills/__init__.py")
    _write(root, "agent/skills/facade.py", source)
    return check_compatibility(root, registry=_symbol_registry(), policy={}, baseline={})


def test_registered_exact_bridge_is_accepted() -> None:
    assert is_registered_bridge("agent.tools.process_tree", "agent.process.tree", _registry())


def test_sibling_and_transitive_bridge_expansion_are_rejected() -> None:
    registry = _registry()
    for destination in ("agent.process.streams", "agent.process.tree.child", "agent.operation.catalog"):
        finding = validate_bridge_edge("agent.tools.process_tree", destination, registry)
        assert finding is not None
        assert finding.code == "W21-COMP-UNKNOWN-BRIDGE"


def test_forbidden_wave_and_service_bag_shapes_are_rejected() -> None:
    findings = check_source_text("if wave21_compatibility: services: Any", "agent/fake.py")
    assert {item.code for item in findings} == {"W21-COMP-WAVE-BRANCH", "W21-COMP-SERVICE-BAG"}


def test_relative_and_module_alias_legacy_consumers_are_rejected() -> None:
    absolute = check_source_text("from agent.runtime.paths import MEMORY_FILE", "agent/new_consumer.py")
    relative = check_source_text("from .paths import MEMORY_FILE", "agent/runtime/new_consumer.py")
    alias = check_source_text("import agent.runtime.paths as paths\nvalue = paths.MEMORY_FILE", "agent/new_consumer.py")
    assert any(item.code == "W21-COMP-LEGACY-PATH" for item in absolute)
    assert any(item.code == "W21-COMP-LEGACY-PATH" for item in relative)
    assert any(item.code == "W21-COMP-LEGACY-PATH" for item in alias)

@pytest.mark.parametrize(
    "source",
    (
        "from agent.operation.catalog import SkillSpec\n",
        "from agent.operation import catalog\nSkillSpec = catalog.SkillSpec\n",
        "from agent.operation.spec import Other as SkillSpec\n",
        "from agent.operation.spec import Other as wrong\nSkillSpec = wrong\n",
        "from agent.skills.other import SkillSpec\n",
        "from agent.operation.spec import *\n",
    ),
)
def test_wrong_owner_symbol_bridge_spellings_fail_closed(tmp_path: Path, source: str) -> None:
    findings = _symbol_fixture(tmp_path, source)
    assert any(item.code == "W21-COMP-WRONG-SYMBOL-BRIDGE" for item in findings)


def test_exact_facade_bridge_passes_end_to_end(tmp_path: Path) -> None:
    _write(tmp_path, "agent/__init__.py")
    _write(tmp_path, "agent/tools/__init__.py")
    _write(tmp_path, "agent/tools/process_tree.py", "from agent.process.tree import run\n")
    _write(tmp_path, "agent/process/__init__.py")
    _write(tmp_path, "agent/process/tree.py")
    _copy_projections(tmp_path)
    findings = check_compatibility(tmp_path, registry=_module_registry())
    assert not findings


def test_sibling_facade_expansion_fails_end_to_end(tmp_path: Path) -> None:
    _write(tmp_path, "agent/__init__.py")
    _write(tmp_path, "agent/tools/__init__.py")
    _write(tmp_path, "agent/tools/process_tree.py", "from agent.process.streams import run\n")
    _write(tmp_path, "agent/process/__init__.py")
    _write(tmp_path, "agent/process/streams.py")
    _copy_projections(tmp_path)
    findings = check_compatibility(tmp_path, registry=_module_registry())
    assert any(item.code == "W21-COMP-UNKNOWN-BRIDGE" for item in findings)


def test_facade_cannot_hide_forbidden_canonical_dependency(tmp_path: Path) -> None:
    _write(tmp_path, "agent/__init__.py")
    _write(tmp_path, "agent/tools/__init__.py")
    _write(tmp_path, "agent/tools/process_tree.py", "from agent.planning import policy\n")
    _write(tmp_path, "agent/planning/__init__.py")
    _write(tmp_path, "agent/planning/policy.py")
    _copy_projections(tmp_path)
    findings = check_compatibility(tmp_path, registry=_module_registry())
    assert any(item.code == "W21-COMP-FORBIDDEN-CANONICAL" for item in findings)


@pytest.mark.parametrize(
    ("source", "relative"),
    (
        ("from agent.runtime.paths import *\n", "agent/new_consumer.py"),
        ("from .paths import *\n", "agent/runtime/new_consumer.py"),
    ),
)
def test_wildcard_legacy_imports_are_closed_world_consumers(source: str, relative: str) -> None:
    findings = check_source_text(source, relative)
    assert len([item for item in findings if item.code == "W21-COMP-LEGACY-PATH"]) == 8


def test_new_wildcard_consumption_fails_even_when_baseline_edge_exists(tmp_path: Path) -> None:
    _write(tmp_path, "agent/__init__.py")
    _write(tmp_path, "agent/runtime/__init__.py")
    _write(tmp_path, "agent/runtime/paths.py")
    _write(
        tmp_path,
        "agent/runtime/consumer.py",
        "from agent.runtime.paths import MEMORY_FILE\nfrom agent.runtime.paths import *\n",
    )
    baseline = {
        "legacy_path_surface": {
            "baseline_active_consumers": [
                {"consumer_module": "agent.runtime.consumer", "symbol": "MEMORY_FILE"}
            ]
        }
    }
    findings = check_compatibility(tmp_path, registry={}, policy={}, baseline=baseline)
    assert any(item.code == "W21-COMP-LEGACY-PATH" for item in findings)


def test_baseline_legacy_consumer_retained_is_allowed() -> None:
    actual = legacy_consumers(RepositorySource(ROOT))
    expected = {
        (str(item["consumer_module"]), str(item["symbol"]))
        for item in json.loads((ROOT / "quality/architecture_baseline.json").read_text(encoding="utf-8"))[
            "legacy_path_surface"
        ]["baseline_active_consumers"]
    }
    assert actual == expected


def test_removed_baseline_consumer_is_not_an_introduction(tmp_path: Path) -> None:
    _write(tmp_path, "agent/__init__.py")
    _write(tmp_path, "agent/runtime/__init__.py")
    _write(tmp_path, "agent/runtime/paths.py")
    _write(tmp_path, "agent/runtime/consumer.py")
    baseline = {
        "legacy_path_surface": {
            "baseline_active_consumers": [
                {"consumer_module": "agent.runtime.consumer", "symbol": "MEMORY_FILE"}
            ]
        }
    }
    assert check_compatibility(tmp_path, registry={}, policy={}, baseline=baseline) == []


def test_reintroduced_baseline_consumer_as_wildcard_is_an_expansion(tmp_path: Path) -> None:
    _write(tmp_path, "agent/__init__.py")
    _write(tmp_path, "agent/runtime/__init__.py")
    _write(tmp_path, "agent/runtime/paths.py")
    consumer = tmp_path / "agent/runtime/consumer.py"
    consumer.write_text("from agent.runtime.paths import MEMORY_FILE\n", encoding="utf-8")
    baseline = {
        "legacy_path_surface": {
            "baseline_active_consumers": [
                {"consumer_module": "agent.runtime.consumer", "symbol": "MEMORY_FILE"}
            ]
        }
    }
    assert check_compatibility(tmp_path, registry={}, policy={}, baseline=baseline) == []
    consumer.write_text("from agent.runtime.paths import *\n", encoding="utf-8")
    findings = check_compatibility(tmp_path, registry={}, policy={}, baseline=baseline)
    assert any(item.code == "W21-COMP-LEGACY-PATH" for item in findings)


def test_closed_world_rejects_absent_registered_surface(tmp_path: Path) -> None:
    findings = _symbol_fixture(tmp_path, "")
    assert any(item.code == "W21-COMP-WRONG-SYMBOL-BRIDGE" for item in findings)


def test_closed_world_rejects_missing_registered_source_module(tmp_path: Path) -> None:
    _write(tmp_path, "agent/__init__.py")
    registry = {
        "bridges": [
            {
                "bridge_id": "MISSING-SOURCE",
                "class": "compatibility",
                "surface": "agent.skills.missing.SkillSpec",
                "target_owner": "agent.operation.spec.SkillSpec",
            }
        ]
    }
    findings = check_compatibility(tmp_path, registry=registry, policy={}, baseline={})
    assert any(item.code == "W21-COMP-WRONG-SYMBOL-BRIDGE" for item in findings)


# Pre-R002 provenance/semantic tests removed; bounded binding coverage is isolated.
def _lifecycle_result(
    root: Path,
    source: str,
    *,
    lane: str = "Z",
    mode: str = "transition",
    activated_lanes: tuple[str, ...] = (),
):
    _write(root, "agent/__init__.py")
    _write(root, "agent/skills/__init__.py")
    _write(root, "agent/skills/facade.py", source)
    return analyze_registered_bridges(
        root,
        registry=_lifecycle_registry(lane=lane),
        mode=mode,
        activated_lanes=activated_lanes,
    )[0]


@pytest.mark.parametrize(
    ("source", "analyzer_state"),
    (
        ("from agent.skills.other import SkillSpec\n", "INVALID"),
        ("SkillSpec = None\n", "INVALID"),
        ("", "UNBOUND"),
    ),
)
def test_unactivated_lane_defers_policy_without_relaxing_analyzer(
    tmp_path: Path,
    source: str,
    analyzer_state: str,
) -> None:
    result = _lifecycle_result(tmp_path, source)
    assert result.analysis.state == analyzer_state
    assert result.lifecycle == BridgeLifecycle.PENDING_MIGRATION
    assert check_compatibility(
        tmp_path,
        registry=_lifecycle_registry(),
        policy={},
        baseline={},
        mode="transition",
    ) == []


def test_unactivated_exact_target_is_accepted_without_pending(tmp_path: Path) -> None:
    result = _lifecycle_result(
        tmp_path,
        "from agent.operation.spec import SkillSpec\n",
    )
    assert result.analysis.state == "EXACT_SYMBOL"
    assert result.lifecycle == BridgeLifecycle.EXACT_TARGET


@pytest.mark.parametrize(
    "source",
    (
        "from agent.skills.other import SkillSpec\n",
        "SkillSpec = None\n",
        "",
        "from agent.operation.catalog import SkillSpec\n",
    ),
)
def test_activated_lane_requires_exact_registered_target(tmp_path: Path, source: str) -> None:
    result = _lifecycle_result(
        tmp_path,
        source,
        lane="Z",
        activated_lanes=("Z",),
    )
    assert result.lifecycle == BridgeLifecycle.VIOLATION
    findings = check_compatibility(
        tmp_path,
        registry=_lifecycle_registry(),
        policy={},
        baseline={},
        mode="transition",
        activated_lanes=("Z",),
    )
    assert [item.code for item in findings] == ["W21-COMP-WRONG-SYMBOL-BRIDGE"]


def test_strict_requires_exact_registered_target(tmp_path: Path) -> None:
    result = _lifecycle_result(
        tmp_path,
        "SkillSpec = None\n",
        mode="strict",
    )
    assert result.analysis.state == "INVALID"
    assert result.lifecycle == BridgeLifecycle.VIOLATION


def test_lifecycle_activation_is_generic_and_lane_local(tmp_path: Path) -> None:
    registry = _lifecycle_registry(lane="Z")
    registry["bridges"].append(
        {
            "bridge_id": "TEST-SYMBOL-BRIDGE-Y",
            "class": "compatibility",
            "surface": "agent.skills.facade.Other",
            "target_owner": "agent.operation.spec.Other",
            "lane": "Y",
        }
    )
    _write(tmp_path, "agent/__init__.py")
    _write(tmp_path, "agent/skills/__init__.py")
    _write(
        tmp_path,
        "agent/skills/facade.py",
        "SkillSpec = None\nOther = None\n",
    )
    results = analyze_registered_bridges(
        tmp_path,
        registry=registry,
        mode="transition",
        activated_lanes=("Z",),
    )
    assert [result.lifecycle for result in results] == [
        BridgeLifecycle.VIOLATION,
        BridgeLifecycle.PENDING_MIGRATION,
    ]


def test_multiple_activated_lanes_form_an_explicit_union(tmp_path: Path) -> None:
    registry = _lifecycle_registry(lane="Z")
    registry["bridges"].append(
        {
            "bridge_id": "TEST-SYMBOL-BRIDGE-Y",
            "class": "compatibility",
            "surface": "agent.skills.facade.Other",
            "target_owner": "agent.operation.spec.Other",
            "lane": "Y",
        }
    )
    _write(tmp_path, "agent/__init__.py")
    _write(tmp_path, "agent/skills/__init__.py")
    _write(
        tmp_path,
        "agent/skills/facade.py",
        "from agent.operation.spec import SkillSpec\nOther = None\n",
    )
    z_only = analyze_registered_bridges(
        tmp_path,
        registry=registry,
        mode="transition",
        activated_lanes=("Z",),
    )
    both = analyze_registered_bridges(
        tmp_path,
        registry=registry,
        mode="transition",
        activated_lanes=("Z", "Y"),
    )
    assert [result.lifecycle for result in z_only] == [
        BridgeLifecycle.EXACT_TARGET,
        BridgeLifecycle.PENDING_MIGRATION,
    ]
    assert [result.lifecycle for result in both] == [
        BridgeLifecycle.EXACT_TARGET,
        BridgeLifecycle.VIOLATION,
    ]


def test_current_meta_bridges_are_pending_by_default_and_required_when_activated() -> None:
    default_findings = check_compatibility(ROOT)
    assert default_findings == []

    activated_findings = check_compatibility(ROOT, mode="transition", activated_lanes=("A",))
    assert [item.detail.split(":", 1)[0] for item in activated_findings] == [
        "W21-CB-META-001",
        "W21-CB-META-002",
        "W21-CB-META-003",
        "W21-CB-META-004",
        "W21-CB-META-005",
    ]

    strict_findings = check_compatibility(ROOT, mode="strict")
    assert [item.detail.split(":", 1)[0] for item in strict_findings] == [
        "W21-CB-META-001",
        "W21-CB-META-002",
        "W21-CB-META-003",
        "W21-CB-META-004",
        "W21-CB-META-005",
    ]


# C005 runtime-interpreter probes removed under AMENDMENT-003.
# C006 exception-flow interpreter probes removed under AMENDMENT-003.
# C007 rejected runtime-interpreter evidence retained in history, not as tests.

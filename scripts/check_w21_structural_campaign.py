"""Fresh bounded structural adversarial campaign for W21-F-R002."""

from __future__ import annotations

import ast
import json
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.check_w21_compatibility import (  # noqa: E402
    BridgeLifecycle,
    analyze_bridge,
    evaluate_bridge_lifecycle,
    validate_bridge_edge,
)
from scripts.w21_architecture.compatibility import normalize_registry  # noqa: E402
from scripts.w21_architecture.structural import (  # noqa: E402
    StructuralStatus,
    analyze_symbol_bridge,
)

TARGET_MODULE = "agent.operation.spec"
TARGET_SYMBOL = "SkillSpec"
SOURCE_MODULE = "agent.skills.facade"
SOURCE_SYMBOL = "SkillSpec"

SYMBOL_REGISTRY = {
    "bridges": [
        {
            "bridge_id": "CAMPAIGN-SYMBOL",
            "class": "compatibility",
            "surface": f"{SOURCE_MODULE}.{SOURCE_SYMBOL}",
            "target_owner": f"{TARGET_MODULE}.{TARGET_SYMBOL}",
            "lane": "A",
        }
    ]
}
MODULE_REGISTRY = {
    "bridges": [
        {
            "bridge_id": "CAMPAIGN-MODULE",
            "class": "compatibility",
            "surface": "agent.tools.process_tree",
            "target_owner": "agent.process.tree",
        }
    ]
}


@dataclass(frozen=True)
class Probe:
    family: str
    label: str
    expected_accept: bool
    evaluate: Callable[[], bool]


def _symbol(
    source: str,
    expected: StructuralStatus,
    *,
    family: str | None = None,
) -> Probe:
    return Probe(
        family
        or (
            "exact canonical ImportFrom"
            if expected is StructuralStatus.EXACT
            else "wrong owner/symbol"
            if expected is StructuralStatus.INVALID
            else "competing direct binding"
        ),
        source.strip().splitlines()[0],
        expected is StructuralStatus.EXACT,
        lambda: analyze_symbol_bridge(
            ast.parse(source),
            SOURCE_SYMBOL,
            target_module=TARGET_MODULE,
            target_symbol=TARGET_SYMBOL,
        ).status
        is StructuralStatus.EXACT,
    )


def _indirect(source: str, label: str) -> Probe:
    return Probe(
        "alternative/indirect bridge syntax",
        label,
        False,
        lambda: analyze_symbol_bridge(
            ast.parse(source),
            SOURCE_SYMBOL,
            target_module=TARGET_MODULE,
            target_symbol=TARGET_SYMBOL,
        ).status
        is StructuralStatus.EXACT,
    )


def _edge(family: str, source: str, destination: str, expected: bool, label: str) -> Probe:
    registry = MODULE_REGISTRY
    return Probe(
        family,
        label,
        expected,
        lambda: validate_bridge_edge(source, destination, registry) is None,
    )


def _lifecycle(
    source: str,
    mode: str,
    activated: tuple[str, ...],
    expected: BridgeLifecycle,
    label: str,
) -> Probe:
    bridge = normalize_registry(SYMBOL_REGISTRY).symbol_bridges[0]
    structural = analyze_bridge(
        ast.parse(source),
        SOURCE_MODULE,
        SOURCE_SYMBOL,
        target_module=TARGET_MODULE,
        target_symbol=TARGET_SYMBOL,
    )
    return Probe(
        "lifecycle transition/activated/strict",
        label,
        expected is not BridgeLifecycle.VIOLATION,
        lambda: evaluate_bridge_lifecycle(
            bridge,
            structural,
            mode=mode,
            activated_lanes=activated,
        )
        is not BridgeLifecycle.VIOLATION,
    )


def probes() -> tuple[Probe, ...]:
    exact = (
        _symbol("from agent.operation.spec import SkillSpec\n", StructuralStatus.EXACT),
        _symbol("from agent.operation.spec import SkillSpec as SkillSpec\n", StructuralStatus.EXACT),
        _symbol("if enabled:\n    from agent.operation.spec import SkillSpec\n", StructuralStatus.EXACT),
        _symbol("try:\n    from agent.operation.spec import SkillSpec\nexcept Exception:\n    pass\n", StructuralStatus.EXACT),
    )
    wrong = tuple(
        _symbol(source, StructuralStatus.INVALID)
        for source in (
            "from agent.operation.catalog import SkillSpec\n",
            "from agent.operation.spec import Other as SkillSpec\n",
            "from agent.skills.facade import SkillSpec\n",
            "from .operation.spec import SkillSpec\n",
            "from agent.operation.spec import *\n",
        )
    )
    indirect = tuple(
        _indirect(source, label)
        for label, source in (
            ("module alias then attribute copy", "import agent.operation.spec as spec\nSkillSpec = spec.SkillSpec\n"),
            ("imported module copied through alias", "from agent.operation import spec\nSkillSpec = spec.SkillSpec\n"),
            ("imported symbol copied through alias", "from agent.operation.spec import SkillSpec as exact\nSkillSpec = exact\n"),
            ("dynamic call result assignment", "SkillSpec = factory(agent.operation.spec.SkillSpec)\n"),
            ("wildcard plus unrelated assignment", "from agent.operation.spec import *\nSkillSpec = replacement\n"),
        )
    )
    competing = tuple(
        _symbol(source, StructuralStatus.AMBIGUOUS)
        for source in (
            "from agent.operation.spec import SkillSpec\nSkillSpec = replacement\n",
            "SkillSpec = replacement\nfrom agent.operation.spec import SkillSpec\n",
            "from agent.operation.spec import SkillSpec\nfrom agent.operation.spec import SkillSpec\n",
            "from agent.operation.spec import SkillSpec\nfrom agent.operation.catalog import SkillSpec\n",
            "from agent.operation.spec import SkillSpec\nif enabled:\n    SkillSpec = replacement\n",
            "from agent.operation.spec import SkillSpec\nvalue = (SkillSpec := replacement)\n",
        )
    )
    control_flow = tuple(
        _symbol(
            source,
            expected,
            family="nested module-scope control-flow binding",
        )
        for source, expected in (
            ("if FLAG:\n    from agent.operation.spec import SkillSpec\nelse:\n    pass\n", StructuralStatus.EXACT),
            ("for item in items:\n    from agent.operation.spec import SkillSpec\n", StructuralStatus.EXACT),
            ("with context():\n    from agent.operation.spec import SkillSpec\n", StructuralStatus.EXACT),
            ("try:\n    pass\nexcept Exception:\n    from agent.operation.spec import SkillSpec\n", StructuralStatus.EXACT),
            ("match value:\n    case _:\n        from agent.operation.spec import SkillSpec\n", StructuralStatus.EXACT),
            ("if FLAG:\n    from agent.operation.spec import SkillSpec\nelse:\n    from agent.operation.catalog import SkillSpec\n", StructuralStatus.AMBIGUOUS),
        )
    )
    edges = (
        _edge("unregistered bridge", "agent.tools.process_tree", "agent.process.tree", True, "registered canonical module edge"),
        _edge("unregistered bridge", "agent.tools.unknown", "agent.process.tree", False, "unknown source edge"),
        _edge("unregistered bridge", "agent.tools.process_tree", "agent.process.streams", False, "wrong destination edge"),
        _edge("unregistered bridge", "agent.tools.process_tree", "agent.operation.catalog", False, "unregistered package edge"),
        _edge("sibling/transitive facade expansion", "agent.tools.process_tree", "agent.process.streams", False, "sibling owner"),
        _edge("sibling/transitive facade expansion", "agent.tools.process_tree", "agent.process.tree.child", False, "transitive child"),
        _edge("sibling/transitive facade expansion", "agent.tools.process_tree", "agent.process", False, "owner package"),
        _edge("sibling/transitive facade expansion", "agent.tools.process_tree", "agent.process.tree", True, "exact owner"),
    )
    lifecycle = (
        _lifecycle("from agent.operation.spec import SkillSpec\n", "transition", (), BridgeLifecycle.EXACT_TARGET, "exact default transition"),
        _lifecycle("from agent.operation.catalog import SkillSpec\n", "transition", (), BridgeLifecycle.PENDING_MIGRATION, "wrong target pending future lane"),
        _lifecycle("from agent.operation.catalog import SkillSpec\n", "transition", ("A",), BridgeLifecycle.VIOLATION, "wrong target activated lane"),
        _lifecycle("from agent.operation.catalog import SkillSpec\n", "strict", (), BridgeLifecycle.VIOLATION, "wrong target strict"),
        _lifecycle("if FLAG:\n    from agent.operation.spec import SkillSpec\n", "strict", ("A",), BridgeLifecycle.EXACT_TARGET, "exact nested strict"),
    )
    return exact + wrong + indirect + competing + control_flow + edges + lifecycle


def run() -> dict[str, object]:
    results = []
    for probe in probes():
        actual = bool(probe.evaluate())
        results.append({"family": probe.family, "label": probe.label, "expected": probe.expected_accept, "actual": actual})
    mismatches = [item for item in results if item["expected"] != item["actual"]]
    families = Counter(str(item["family"]) for item in results)
    return {
        "total": len(results),
        "families": dict(sorted(families.items())),
        "accepts": sum(bool(item["actual"]) for item in results),
        "rejects": sum(not bool(item["actual"]) for item in results),
        "mismatches": len(mismatches),
        "mismatch_labels": [str(item["label"]) for item in mismatches],
    }


def main() -> int:
    report = run()
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["total"] >= 32 and len(report["families"]) >= 8 and report["mismatches"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

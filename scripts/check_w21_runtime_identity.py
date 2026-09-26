"""Separate runtime identity fixture for eligible W21 symbol bridges."""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.w21_architecture.compatibility import normalize_registry  # noqa: E402


def _lanes(values: Iterable[str]) -> frozenset[str]:
    if isinstance(values, str):
        values = (values,)
    return frozenset(str(value).strip() for value in values if str(value).strip())


def _parts(surface: str) -> tuple[str, str]:
    module, symbol = surface.rsplit(".", 1)
    return module, symbol


def check_identity(
    registry: Mapping[str, Any],
    *,
    activated_lanes: Iterable[str] = (),
) -> dict[str, object]:
    """Check actual object identity for active registered symbol bridges only."""

    active = _lanes(activated_lanes)
    checked: list[str] = []
    pending: list[str] = []
    failures: list[str] = []
    for bridge in normalize_registry(registry).symbol_bridges:
        lane = bridge.raw.get("lane")
        if not isinstance(lane, str) or not lane.strip() or lane.strip() not in active:
            pending.append(bridge.bridge_id)
            continue
        source_module, source_symbol = bridge.source_module, bridge.source_symbol
        target_module, target_symbol = bridge.target_module, bridge.target_symbol
        try:
            legacy_symbol = getattr(importlib.import_module(source_module), source_symbol)
            canonical_symbol = getattr(importlib.import_module(target_module), target_symbol)
        except (ImportError, AttributeError) as exc:
            failures.append(f"{bridge.bridge_id}: cannot import identity fixture: {exc}")
            continue
        checked.append(bridge.bridge_id)
        if legacy_symbol is not canonical_symbol:
            failures.append(f"{bridge.bridge_id}: legacy symbol is not canonical symbol")
    return {
        "checked": checked,
        "pending": pending,
        "failures": failures,
        "passed": not failures,
    }


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else {}


def main() -> int:
    parser = argparse.ArgumentParser(description="Check active W21 runtime symbol identity")
    parser.add_argument("--registry", type=Path, default=ROOT / "quality/architecture_compatibility.json")
    parser.add_argument("--activated-lane", action="append", default=[])
    arguments = parser.parse_args()
    report = check_identity(_load(arguments.registry), activated_lanes=arguments.activated_lane)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

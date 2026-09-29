"""Validate the closed W22 matrix for remaining Agent runtime modules."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
RUNTIME_ROOT = ROOT / "src" / "llm_agent" / "agent" / "runtime"
MATRIX_PATH = ROOT / "quality" / "runtime_w22_dispositions.json"
ALLOWED = {
    "PLATFORM_GENERIC",
    "AGENT_RUNTIME",
    "MOVE_TO_SPECIFIC_AGENT_OWNER",
    "COMPATIBILITY_HISTORICAL",
}


def _document() -> dict[str, Any]:
    value = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("runtime disposition matrix must be an object")
    return value


def _module_set_errors(actual: set[str], declared: set[str]) -> list[str]:
    errors = [f"unclassified Agent runtime module: {name}" for name in sorted(actual - declared)]
    errors.extend(f"stale Agent runtime disposition: {name}" for name in sorted(declared - actual))
    return errors


def _disposition_errors(raw: dict[str, Any]) -> list[str]:
    return [
        f"unsupported runtime disposition for {name}: {disposition!r}"
        for name, disposition in sorted(raw.items())
        if disposition not in ALLOWED
    ]


def _facade_errors(raw: dict[str, Any], facades: set[str]) -> list[str]:
    errors = [
        f"compatibility facade is not classified as historical: {name}"
        for name in sorted(facades)
        if raw.get(name) != "COMPATIBILITY_HISTORICAL"
    ]
    errors.extend(
        f"historical runtime disposition is not listed as a facade: {name}"
        for name, disposition in raw.items()
        if disposition == "COMPATIBILITY_HISTORICAL" and name not in facades
    )
    return errors


def validate() -> list[str]:
    document = _document()
    errors: list[str] = []
    if document.get("status") != "CLOSED":
        errors.append("runtime disposition matrix is not CLOSED")
    raw = document.get("dispositions")
    if not isinstance(raw, dict):
        return ["runtime disposition matrix must contain a dispositions object"]
    actual = {path.name for path in RUNTIME_ROOT.glob("*.py")}
    declared = set(raw)
    errors.extend(_module_set_errors(actual, declared))
    errors.extend(_disposition_errors(raw))
    facades = set(document.get("compatibility_facades", []))
    errors.extend(_facade_errors(raw, facades))
    return sorted(set(errors))


def main() -> int:
    errors = validate()
    if errors:
        for error in errors:
            print(f"FAIL: {error}")
        return 1
    document = _document()
    raw = document["dispositions"]
    counts = {name: sum(value == name for value in raw.values()) for name in ALLOWED}
    print("W22 runtime dispositions: PASS")
    print(f"classified modules: {len(raw)}")
    for name in sorted(counts):
        print(f"{name}: {counts[name]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

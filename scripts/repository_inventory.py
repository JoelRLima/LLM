"""Classify root entries; optionally remove only known disposable tool caches."""
from __future__ import annotations

import argparse
import json
import shutil
import stat
from pathlib import Path

CACHES = frozenset({".pytest_cache", ".mypy_cache", ".ruff_cache"})
SOURCES = frozenset({"src", "tests", "scripts", "docs", "distribution", "installer", "examples", ".github", "quality"})
STATE = frozenset({".git", ".venv", "runtime", ".workbench-managed", ".agent-local", ".agents", ".codex"})


def category(path: Path) -> str:
    if path.name in CACHES:
        return "disposable_cache"
    if path.name in SOURCES:
        return "source"
    if path.name in STATE or path.name.startswith(".workbench-installed") or path.name in {"config.json", ".env"}:
        return "protected_local_state"
    if path.name in {"reports", ".audit-local", ".tmp"}:
        return "local_evidence"
    if path.name == "prototypes":
        return "experiments_and_historical_evidence"
    return "review_manually" if path.is_dir() else "root_file"


def linked(path: Path) -> bool:
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def remove_cache(root: Path, path: Path) -> None:
    # Verify absolute Windows targets before recursion. Never follow reparse
    # entries into user state, installations, evidence or the workspace itself.
    if path.name not in CACHES or path.parent != root or path.resolve().parent != root or linked(path):
        raise ValueError(f"Unsafe cache target: {path}")
    if any(linked(child) for child in path.rglob("*")):
        raise ValueError(f"Linked descendant in cache: {path}")
    shutil.rmtree(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--dry-run", action="store_true", help="Default: inventory only")
    parser.add_argument("--apply-cache-cleanup", action="store_true", help="Remove only .pytest_cache, .mypy_cache and .ruff_cache")
    args = parser.parse_args()
    if args.dry_run and args.apply_cache_cleanup:
        parser.error("choose dry-run or apply")
    root = args.root.resolve(strict=True)
    if not (root / "pyproject.toml").is_file() or not (root / "src/llm_agent").is_dir():
        parser.error("root is not an Agent checkout")
    rows = []
    for path in sorted(root.iterdir()):
        kind = category(path)
        row = {"path": path.name, "category": kind, "removed": False}
        if args.apply_cache_cleanup and kind == "disposable_cache":
            remove_cache(root, path)
            row["removed"] = True
        rows.append(row)
    print(json.dumps({"dry_run": not args.apply_cache_cleanup, "entries": rows}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
